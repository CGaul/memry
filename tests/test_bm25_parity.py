"""The portable keyword index scores exactly as SQLite FTS5's ``bm25()``.

``memry.backends.keyword`` re-implements FTS5's tokenizer and BM25 over plain
tables so that a second database engine ranks memories as SQLite does. These
tests hold it to FTS5 itself: the same terms for every code point, and the
same scores and ranking for the same corpus and question, also after rows are
changed and removed. Set ``MEMRY_TEST_PG_URL`` to run the index on
PostgreSQL as well.
"""

from __future__ import annotations

import json
import math
import os
import random
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from memry.backends.keyword import PostingIndex, Unicode61, bm25_idf

ROOT = Path(__file__).resolve().parent.parent
TOKENIZER = Unicode61()

#: Extra texts in the scripts and spellings a memory store meets: accents
#: written whole and as combining marks, German, Turkish, Greek, Cyrillic,
#: CJK, Arabic, emoji, digits glued to letters.
EXTRA = [
    "Ada zog nach München, nicht nach München ohne Umlaut, und aß Grießbrei.",
    "Straße STRASSE straße — Größe, Übermaß, ÄÖÜ äöü",
    "İstanbul ılık İSTANBUL; Ελληνικά ΣΊΣΥΦΟΣ σίσυφος; Москва МОСКВА москва",
    "東京で寿司を食べた。北京烤鸭。서울 맛집",
    "مرحبا بالعالم — שלום עולם",
    "deploy v2.3.1 on 2026-10-19 at 10:42, PR #2301, issue #42, 3x faster",
    "café CAFÉ café naïve résumé Œuvre Æsir ÐÞ ǅemal",
    "emoji 🚀 rocket 🧠 brain 👩‍💻 developer",
    "snake_case kebab-case camelCase ALLCAPS dotted.name path/to/file.py",
    "Ａｄａ Ｋｒａｕｓｅ （fullwidth） ① ② ³ ¼ Ⅻ ⅻ",
    "Deseret 𐐀𐐁𐐂 and 𐐨𐐩𐐪 mathematical 𝐀𝐁𝐂",
]


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value] if len(value) > 3 else []
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def corpus() -> list[str]:
    texts: list[str] = []
    for path in sorted((ROOT / "evals" / "datasets").glob("*.json*")):
        lines = [path.read_text()] if path.suffix == ".json" else path.read_text().splitlines()
        for line in lines:
            if line.strip():
                texts.extend(_strings(json.loads(line)))
    for path in sorted((ROOT / "tests" / "fixtures").glob("*.json")):
        texts.extend(_strings(json.loads(path.read_text())))
    texts.extend(EXTRA)
    seen: set[str] = set()
    unique = [t for t in texts if not (t in seen or seen.add(t))]
    # documents built from random code points, so the corpus also holds
    # what no fixture spells
    rng = random.Random(7)
    for _ in range(200):
        unique.append("".join(chr(rng.choice((rng.randrange(32, 0x2FFF),
                                              rng.randrange(0x1F300, 0x1FAFF))))
                              for _ in range(rng.randrange(5, 60))))
    return unique


def fts5(texts: dict[int, str]) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE VIRTUAL TABLE m USING fts5(content)")
    db.executemany("INSERT INTO m(rowid, content) VALUES (?, ?)", texts.items())
    db.execute("CREATE VIRTUAL TABLE v USING fts5vocab(m, row)")
    return db


def portable_sqlite(texts: dict[int, str]) -> PostingIndex:
    index = PostingIndex(sqlite3.connect(":memory:"))
    index.create_schema()
    for rowid, text in texts.items():
        index.put("memories", str(rowid), text)
    return index


def fts5_bm25(db: sqlite3.Connection, phrases: list[str]) -> dict[str, float]:
    query = " OR ".join('"' + p.replace('"', '""') + '"' for p in phrases)
    return {str(rowid): score for rowid, score in
            db.execute("SELECT rowid, bm25(m) FROM m WHERE m MATCH ?", (query,))}


def questions(db: sqlite3.Connection, seed: int = 1) -> list[list[str]]:
    """Single terms of every frequency band, and mixes of them with repeats,
    as ``_keyword_scores`` asks them."""
    rng = random.Random(seed)
    terms = [t for (t,) in db.execute("SELECT term FROM v ORDER BY doc DESC, term")]
    common, rare = terms[:25], terms[-200:]
    middle = terms[25:-200] or terms
    singles = [[t] for t in common + rng.sample(middle, min(60, len(middle))) +
               rng.sample(rare, min(40, len(rare)))]
    mixes = []
    for _ in range(80):
        pick = rng.sample(terms, rng.randrange(2, 6))
        pick += rng.sample(pick, rng.randrange(0, 2))  # a word asked twice
        mixes.append(pick)
    return singles + mixes + [["a-term-no-document-holds"], ["the", "zzzz-missing"]]


def assert_same(expected: dict[str, float], got: dict[str, float], phrases: list[str]) -> float:
    assert set(got) == set(expected), phrases
    worst = 0.0
    for doc, score in expected.items():
        worst = max(worst, abs(score - got[doc]))
        assert got[doc] == pytest.approx(score, rel=1e-12, abs=1e-12), (phrases, doc)
    rank = lambda scores: sorted(scores, key=lambda d: (scores[d], int(d)))  # noqa: E731
    assert rank(got) == rank(expected), phrases
    return worst


# -- the tokenizer ---------------------------------------------------------

def test_every_code_point_splits_and_folds_as_fts5_does():
    """Each code point at the start of a word, inside one, alone and doubled:
    the terms FTS5 indexes for those texts, in order, are ours."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
    db.execute("CREATE VIRTUAL TABLE ti USING fts5vocab(t, instance)")
    codes = [c for c in range(1, 0x110000) if not 0xD800 <= c <= 0xDFFF]
    rows = {}
    for start in range(0, len(codes), 2000):
        rows[len(rows) + 1] = " ".join(f"a{chr(c)}b {chr(c)} {chr(c)}{chr(c)}a"
                                       for c in codes[start:start + 2000])
    db.executemany("INSERT INTO t(rowid, x) VALUES (?, ?)", rows.items())
    indexed: dict[int, list[str]] = {rowid: [] for rowid in rows}
    for term, doc in db.execute("SELECT term, doc FROM ti ORDER BY doc, offset"):
        indexed[doc].append(term)
    mismatched = [rowid for rowid, text in rows.items() if TOKENIZER.tokens(text) != indexed[rowid]]
    assert not mismatched, [rows[r][:200] for r in mismatched[:3]]


def test_a_row_is_as_long_as_fts5_counts_it():
    texts = dict(enumerate(corpus(), start=1))
    db = fts5(texts)
    sizes = dict(db.execute("SELECT id, sz FROM m_docsize"))
    for rowid, text in texts.items():
        stored = sizes[rowid]
        # m_docsize holds the size as an FTS5 varint blob
        value, shift = 0, 0
        for byte in stored:
            value |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                break
        assert len(TOKENIZER.tokens(text)) == value, text[:80]


# -- the scores --------------------------------------------------------------

def test_idf_is_fts5s_including_the_clamp():
    assert bm25_idf(1, 10) == pytest.approx(math.log(9.5 / 1.5))
    assert bm25_idf(9, 10) == 1e-6  # a term in most rows weighs next to nothing


def test_scores_and_ranking_equal_fts5_bm25():
    texts = dict(enumerate(corpus(), start=1))
    db, index = fts5(texts), portable_sqlite(texts)
    worst = 0.0
    for phrases in questions(db):
        worst = max(worst, assert_same(fts5_bm25(db, phrases), index.bm25("memories", phrases),
                                       phrases))
    print(f"largest difference from bm25(): {worst!r}")


def test_parity_survives_updates_and_deletes():
    texts = dict(enumerate(corpus(), start=1))
    db, index = fts5(texts), portable_sqlite(texts)
    rng = random.Random(3)
    rowids = list(texts)
    for rowid in rng.sample(rowids, len(rowids) // 10):
        db.execute("DELETE FROM m WHERE rowid = ?", (rowid,))
        index.delete("memories", str(rowid))
        rowids.remove(rowid)
    for rowid in rng.sample(rowids, len(rowids) // 10):
        text = texts[rng.choice(list(texts))] + " " + texts[rowid]
        db.execute("UPDATE m SET content = ? WHERE rowid = ?", (text, rowid))
        index.put("memories", str(rowid), text)
    rows = db.execute("SELECT count(*) FROM m").fetchone()[0]
    assert index.stats("memories")[0] == rows
    for phrases in questions(db, seed=2):
        assert_same(fts5_bm25(db, phrases), index.bm25("memories", phrases), phrases)


def test_term_counts_equal_fts5vocab():
    texts = dict(enumerate(corpus(), start=1))
    db, index = fts5(texts), portable_sqlite(texts)
    vocab = dict(db.execute("SELECT term, doc FROM v"))
    assert index.hits("memories", vocab) == vocab
    mine = Counter(t for text in texts.values() for t in set(TOKENIZER.tokens(text)))
    assert dict(mine) == vocab


@pytest.mark.skipif(not os.environ.get("MEMRY_TEST_PG_URL"), reason="MEMRY_TEST_PG_URL not set")
def test_postgres_holds_the_same_index_and_scores():
    psycopg = pytest.importorskip("psycopg")
    texts = dict(enumerate(corpus(), start=1))
    db = fts5(texts)
    with psycopg.connect(os.environ["MEMRY_TEST_PG_URL"], autocommit=True) as pg:
        for table in ("kw_docs", "kw_postings", "kw_terms", "kw_stats"):
            pg.execute(f"DROP TABLE IF EXISTS {table}")
        index = PostingIndex(pg, placeholder="%s")
        index.create_schema()
        with pg.transaction():
            for rowid, text in texts.items():
                index.put("memories", str(rowid), text)
        for phrases in questions(db):
            assert_same(fts5_bm25(db, phrases), index.bm25("memories", phrases), phrases)
