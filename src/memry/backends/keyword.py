"""BM25 keyword scoring that no database computes for us.

``LocalBackend`` reads its keyword scores from SQLite FTS5's ``bm25()``. That
ties keyword search to one engine: PostgreSQL has no BM25 (``ts_rank`` is
not BM25) and splits text into words differently, so a second backend would
rank the same memories differently. This module computes the same numbers
from plain tables any SQL database can hold:

- ``Unicode61`` splits and folds text exactly as FTS5's default tokenizer
  does (``unicode61``, ``remove_diacritics=1``, ``categories='L* N* Co'``),
  from the tables SQLite generates out of the Unicode Character Database
  (``_fts5_unicode``, made by ``tools/gen_fts5_unicode.py``);
- ``PostingIndex`` stores, per document, its length in tokens and how often
  each term occurs in it, plus the counts the formula needs (rows, tokens,
  documents per term), written by the application in the caller's
  transaction — no triggers, no full-text extension, no engine function;
- ``bm25_scores`` is FTS5's ``fts5Bm25Function`` (``ext/fts5/fts5_aux.c``)
  in Python: k1 = 1.2, b = 0.75, idf = ln((N - n + 0.5) / (n + 0.5)) with
  a non-positive idf replaced by 1e-6, each phrase's part summed in query
  order and the sum negated (lower is better), with the same operations in
  the same order so that the doubles come out the same.

The database only stores and returns integers; every formula is here. That
is what makes the scores the same on every engine.
"""

from __future__ import annotations

import bisect
import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from functools import lru_cache
from typing import Any, Protocol

from . import _fts5_unicode as U

#: FTS5's BM25 constants (``fts5Bm25Function``).
K1 = 1.2
B = 0.75
#: A token longer than this many bytes is indexed cut to it (``fts5Int.h``).
MAX_TOKEN_BYTES = 32768
#: The categories the default ``unicode61`` treats as token characters;
#: index 0 is always on (``sqlite3Fts5UnicodeCatParse`` sets it first), so a
#: code point the tables do not cover is part of a word.
_TOKEN_CATEGORIES = frozenset(
    [0] + [U.CATEGORY_INDEX[c] for c in ("Ll", "Lm", "Lo", "Lt", "Lu", "LC",
                                          "Nd", "Nl", "No", "Co")])


# -- the tokenizer ------------------------------------------------------

def unicode_category(code: int) -> int:
    """``sqlite3Fts5UnicodeCategory``: the category index of a code point."""
    if code >= 1 << 20:
        return 0
    lo, hi = U.CAT_BLOCK[code >> 16], U.CAT_BLOCK[1 + (code >> 16)]
    key = code & 0xFFFF
    res = -1
    while hi > lo:
        test = (hi + lo) // 2
        if key >= U.CAT_MAP[test]:
            res = test
            lo = test + 1
        else:
            hi = test
    if res < 0:
        return 0
    if key >= U.CAT_MAP[res] + (U.CAT_DATA[res] >> 5):
        return 0
    ret = U.CAT_DATA[res] & 0x1F
    if ret != 30:
        return ret
    return 5 if (key - U.CAT_MAP[res]) & 0x01 else 9


def is_diacritic(code: int) -> bool:
    """``sqlite3Fts5UnicodeIsdiacritic``: a combining mark that continues a
    word without starting one."""
    if code < 768 or code > 817:
        return False
    if code < 768 + 32:
        return bool(U.DIACRITIC_MASK0 & (1 << (code - 768)))
    return bool(U.DIACRITIC_MASK1 & (1 << (code - 768 - 32)))


def _remove_diacritic(code: int) -> int:
    """``fts5_remove_diacritic`` with ``bComplex`` = 0 (``remove_diacritics=1``)."""
    key = (code << 3) | 0x07
    res = bisect.bisect_right(U.DIA, key) - 1
    if U.DIA_CHAR[res] & 0x80:
        return code
    return code if code > (U.DIA[res] >> 3) + (U.DIA[res] & 0x07) else U.DIA_CHAR[res] & 0x7F


def fold(code: int) -> int:
    """``sqlite3Fts5UnicodeFold(code, 1)``: the lower-case code point, with
    the diacritic removed; 0 means the code point is dropped."""
    if code < 128:
        return code + 32 if 65 <= code <= 90 else code
    if code < 65536:
        ret = code
        res = bisect.bisect_right(U.FOLD_CODE, code) - 1
        start, flags, size = U.FOLD_CODE[res], U.FOLD_FLAGS[res], U.FOLD_RANGE[res]
        if code < start + size and 0 == (0x01 & flags & (start ^ code)):
            ret = (code + U.FOLD_OFFSET[flags >> 1]) & 0xFFFF
        return _remove_diacritic(ret)
    if 66560 <= code < 66600:
        return code + 40
    return code


def _is_token_char(code: int) -> bool:
    return unicode_category(code) in _TOKEN_CATEGORIES


def _runs(predicate_ranges: Iterable[tuple[int, int]]) -> str:
    """A regex character class body for these inclusive code point ranges."""
    parts = []
    for lo, hi in predicate_ranges:
        parts.append(re.escape(chr(lo)) if lo == hi
                     else f"{re.escape(chr(lo))}-{re.escape(chr(hi))}")
    return "".join(parts)


def _token_ranges() -> list[tuple[int, int]]:
    """The code points that start a word, as inclusive ranges. UTF-8 cannot
    carry a surrogate, and FTS5 reads one (and U+FFFE/U+FFFF) as U+FFFD."""
    replacement = _is_token_char(0xFFFD)
    ranges: list[tuple[int, int]] = []
    start = None
    for code in range(0x110000):
        if 0xD800 <= code <= 0xDFFF or code in (0xFFFE, 0xFFFF):
            on = replacement
        else:
            on = _is_token_char(code)
        if on and start is None:
            start = code
        elif not on and start is not None:
            ranges.append((start, code - 1))
            start = None
    if start is not None:
        ranges.append((start, 0x10FFFF))
    return ranges


@lru_cache(maxsize=1)
def _word_re() -> re.Pattern[str]:
    starts = _token_ranges()
    marks = [(c, c) for c in range(768, 818) if is_diacritic(c)]
    body = _runs(starts)
    return re.compile(f"[{body}][{body}{_runs(marks)}]*")


@lru_cache(maxsize=1)
def _fold_table() -> dict[int, int | None]:
    """``str.translate`` table of every code point ``fold`` changes."""
    table: dict[int, int | None] = {}
    for code in list(range(65536)) + list(range(66560, 66600)):
        if 0xD800 <= code <= 0xDFFF:
            continue
        folded = fold(code)
        if folded != code:
            table[code] = folded or None
    return table


_ASCII_WORD = re.compile(r"[A-Za-z0-9]+")


class Unicode61:
    """FTS5's ``unicode61`` tokenizer with its defaults, in Python."""

    def tokens(self, text: str) -> list[str]:
        """The terms FTS5 indexes for ``text``, in order: one per token,
        repeats included, each folded and cut to ``MAX_TOKEN_BYTES``."""
        if text.isascii():
            return [w.lower() for w in _ASCII_WORD.findall(text)]
        table = _fold_table()
        out = []
        for word in _word_re().findall(text):
            term = word.translate(table)
            if len(term) * 4 > MAX_TOKEN_BYTES:
                raw = term.encode("utf-8", "surrogatepass")
                if len(raw) > MAX_TOKEN_BYTES:
                    term = raw[:MAX_TOKEN_BYTES].decode("utf-8", "replace")
            out.append(term)
        return out


# -- BM25 ---------------------------------------------------------------

def bm25_idf(hits: int, rows: int) -> float:
    """The idf ``fts5Bm25GetData`` gives a phrase found in ``hits`` of ``rows``."""
    idf = math.log((rows - hits + 0.5) / (hits + 0.5))
    return idf if idf > 0.0 else 1e-6


def bm25_scores(
    phrases: Sequence[str],
    rows: int,
    tokens: int,
    hits: dict[str, int],
    postings: dict[str, dict[Any, int]],
    lengths: dict[Any, int],
) -> dict[Any, float]:
    """``bm25()`` of every document holding at least one phrase, for the
    query ``"p1" OR "p2" OR …`` (each phrase one term, repeats allowed).

    ``hits`` is the number of documents holding each term, ``postings`` the
    term -> document -> frequency, ``lengths`` each document's token count.
    Same operations, same order as ``fts5Bm25Function``: the phrases are
    summed in query order and the total negated."""
    if rows <= 0 or not phrases:
        return {}
    avgdl = float(tokens) / float(rows)
    idf = [bm25_idf(hits.get(p, 0), rows) for p in phrases]
    docs: set[Any] = set()
    for phrase in phrases:
        docs.update(postings.get(phrase, {}))
    out: dict[Any, float] = {}
    for doc in docs:
        d = float(lengths[doc])
        score = 0.0
        for i, phrase in enumerate(phrases):
            freq = float(postings.get(phrase, {}).get(doc, 0))
            score += idf[i] * ((freq * (K1 + 1.0)) / (freq + K1 * (1 - B + B * d / avgdl)))
        out[doc] = -1.0 * score
    return out


# -- the index ------------------------------------------------------------

class Connection(Protocol):
    """The DB-API surface ``PostingIndex`` needs."""

    def execute(self, sql: str, params: Sequence[Any] = ...) -> Any: ...


#: Portable DDL: integer and text columns, composite primary keys, one
#: secondary index. Nothing engine-specific.
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS kw_docs (
        kind TEXT NOT NULL, doc_id TEXT NOT NULL, length INTEGER NOT NULL,
        PRIMARY KEY (kind, doc_id))""",
    """CREATE TABLE IF NOT EXISTS kw_postings (
        kind TEXT NOT NULL, term TEXT NOT NULL, doc_id TEXT NOT NULL, tf INTEGER NOT NULL,
        PRIMARY KEY (kind, term, doc_id))""",
    "CREATE INDEX IF NOT EXISTS kw_postings_doc ON kw_postings (kind, doc_id)",
    """CREATE TABLE IF NOT EXISTS kw_terms (
        kind TEXT NOT NULL, term TEXT NOT NULL, docs INTEGER NOT NULL,
        PRIMARY KEY (kind, term))""",
    """CREATE TABLE IF NOT EXISTS kw_stats (
        kind TEXT NOT NULL PRIMARY KEY, rows_total INTEGER NOT NULL,
        tokens_total INTEGER NOT NULL)""",
)


class PostingIndex:
    """BM25 over plain tables. ``kind`` keeps separate corpora apart (FTS5
    has one table per corpus: memories, episodes). The caller owns the
    transaction: every write here belongs to the write that caused it.

    ``placeholder`` is the only dialect difference: ``?`` for sqlite3,
    ``%s`` for psycopg."""

    def __init__(self, db: Connection, *, placeholder: str = "?",
                 tokenizer: Unicode61 | None = None) -> None:
        self.db = db
        self.p = placeholder
        self.tokenizer = tokenizer or Unicode61()

    def _q(self, sql: str) -> str:
        return sql.replace("?", self.p) if self.p != "?" else sql

    def _run(self, sql: str, params: Sequence[Any] = ()) -> Any:
        return self.db.execute(self._q(sql), tuple(params))

    def _rows(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        return [tuple(r) for r in self._run(sql, params).fetchall()]

    def create_schema(self) -> None:
        for statement in SCHEMA:
            self._run(statement)

    # writes ---------------------------------------------------------------
    def put(self, kind: str, doc_id: str, text: str) -> None:
        """Index ``text`` as ``doc_id``, replacing what it held before."""
        self.delete(kind, doc_id)
        terms = Counter(self.tokenizer.tokens(text))
        length = sum(terms.values())
        self._run("INSERT INTO kw_docs (kind, doc_id, length) VALUES (?, ?, ?)",
                  (kind, doc_id, length))
        for term, tf in terms.items():
            self._run("INSERT INTO kw_postings (kind, term, doc_id, tf) VALUES (?, ?, ?, ?)",
                      (kind, term, doc_id, tf))
            if self._run("UPDATE kw_terms SET docs = docs + 1 WHERE kind = ? AND term = ?",
                         (kind, term)).rowcount == 0:
                self._run("INSERT INTO kw_terms (kind, term, docs) VALUES (?, ?, 1)",
                          (kind, term))
        self._add_stats(kind, 1, length)

    def delete(self, kind: str, doc_id: str) -> None:
        held = self._rows("SELECT length FROM kw_docs WHERE kind = ? AND doc_id = ?",
                          (kind, doc_id))
        if not held:
            return
        for (term,) in self._rows(
                "SELECT term FROM kw_postings WHERE kind = ? AND doc_id = ?", (kind, doc_id)):
            self._run("UPDATE kw_terms SET docs = docs - 1 WHERE kind = ? AND term = ?",
                      (kind, term))
        self._run("DELETE FROM kw_terms WHERE kind = ? AND docs <= 0", (kind,))
        self._run("DELETE FROM kw_postings WHERE kind = ? AND doc_id = ?", (kind, doc_id))
        self._run("DELETE FROM kw_docs WHERE kind = ? AND doc_id = ?", (kind, doc_id))
        self._add_stats(kind, -1, -held[0][0])

    def _add_stats(self, kind: str, rows: int, tokens: int) -> None:
        if self._run("UPDATE kw_stats SET rows_total = rows_total + ?, "
                     "tokens_total = tokens_total + ? WHERE kind = ?",
                     (rows, tokens, kind)).rowcount == 0:
            self._run("INSERT INTO kw_stats (kind, rows_total, tokens_total) VALUES (?, ?, ?)",
                      (kind, rows, tokens))

    # reads ----------------------------------------------------------------
    def stats(self, kind: str) -> tuple[int, int]:
        """Documents and tokens in the corpus (``xRowCount``,
        ``xColumnTotalSize``)."""
        row = self._rows("SELECT rows_total, tokens_total FROM kw_stats WHERE kind = ?", (kind,))
        return (int(row[0][0]), int(row[0][1])) if row else (0, 0)

    def hits(self, kind: str, terms: Iterable[str]) -> dict[str, int]:
        """How many documents hold each term."""
        terms = sorted(set(terms))
        if not terms:
            return {}
        marks = ", ".join("?" for _ in terms)
        found = dict(self._rows(
            f"SELECT term, docs FROM kw_terms WHERE kind = ? AND term IN ({marks})",
            (kind, *terms)))
        return {t: int(found.get(t, 0)) for t in terms}

    def postings(self, kind: str, terms: Iterable[str]) -> tuple[
            dict[str, dict[str, int]], dict[str, int]]:
        """Each term's documents with its frequency, and their lengths."""
        terms = sorted(set(terms))
        if not terms:
            return {}, {}
        marks = ", ".join("?" for _ in terms)
        postings: dict[str, dict[str, int]] = {t: {} for t in terms}
        lengths: dict[str, int] = {}
        for term, doc_id, tf, length in self._rows(
                "SELECT p.term, p.doc_id, p.tf, d.length FROM kw_postings p "
                "JOIN kw_docs d ON d.kind = p.kind AND d.doc_id = p.doc_id "
                f"WHERE p.kind = ? AND p.term IN ({marks})", (kind, *terms)):
            postings[term][doc_id] = int(tf)
            lengths[doc_id] = int(length)
        return postings, lengths

    def bm25(self, kind: str, phrases: Sequence[str]) -> dict[str, float]:
        """``bm25()`` of each document matching ``"p1" OR "p2" OR …``; each
        phrase must be one term as ``Unicode61`` writes it."""
        rows, tokens = self.stats(kind)
        postings, lengths = self.postings(kind, phrases)
        return bm25_scores(phrases, rows, tokens, self.hits(kind, phrases), postings, lengths)
