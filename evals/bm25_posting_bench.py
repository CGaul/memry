"""How fast the portable keyword index is next to FTS5, at a store's size.

    python evals/bm25_posting_bench.py --docs 20000 [--pg-url postgresql://…]

Builds a corpus of ``--docs`` documents from the words of memry's own
datasets (sampled from their running text, so word frequencies stay
natural), indexes it into FTS5 and into ``PostingIndex`` (SQLite, and
PostgreSQL when a URL is given), then times questions of three kinds: one
rare word, one common word, and a mix of four. Every engine's scores are
checked against FTS5 on the way, so a fast wrong answer cannot pass.

The posting index here scores every document that holds a word; it does not
yet use the MaxScore shortcut ``LocalBackend._keyword_scores`` takes for
common words, so the common-word numbers are its worst case.
"""

from __future__ import annotations

import argparse
import heapq
import random
import sqlite3
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from memry.backends.keyword import PostingIndex, Unicode61  # noqa: E402
from test_bm25_parity import corpus, fts5_bm25  # noqa: E402


def build(n: int, seed: int = 11) -> dict[int, str]:
    words = [w for text in corpus()[:-200] for w in text.split()]
    rng = random.Random(seed)
    return {i: " ".join(words[rng.randrange(len(words))] for _ in range(rng.randrange(8, 40)))
            for i in range(1, n + 1)}


def timed(fn, repeat: int = 5) -> float:
    runs = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        runs.append((time.perf_counter() - start) * 1000)
    return statistics.median(runs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", type=int, default=20000)
    parser.add_argument("--pg-url")
    args = parser.parse_args()

    texts = build(args.docs)
    tok = Unicode61()
    tok.tokens("warm up the tables é")

    t0 = time.perf_counter()
    fts = sqlite3.connect(":memory:")
    fts.execute("CREATE VIRTUAL TABLE m USING fts5(content)")
    fts.executemany("INSERT INTO m(rowid, content) VALUES (?, ?)", texts.items())
    fts.commit()
    fts.execute("CREATE VIRTUAL TABLE v USING fts5vocab(m, row)")
    indexing = {"fts5": time.perf_counter() - t0}

    engines: dict[str, PostingIndex] = {}
    t0 = time.perf_counter()
    lite = sqlite3.connect(":memory:")
    engines["posting/sqlite"] = PostingIndex(lite)
    engines["posting/sqlite"].create_schema()
    for rowid, text in texts.items():
        engines["posting/sqlite"].put("memories", str(rowid), text)
    lite.commit()
    indexing["posting/sqlite"] = time.perf_counter() - t0

    if args.pg_url:
        import psycopg

        pg = psycopg.connect(args.pg_url)
        for table in ("kw_docs", "kw_postings", "kw_terms", "kw_stats"):
            pg.execute(f"DROP TABLE IF EXISTS {table}")
        t0 = time.perf_counter()
        engines["posting/postgres"] = PostingIndex(pg, placeholder="%s")
        engines["posting/postgres"].create_schema()
        for rowid, text in texts.items():
            engines["posting/postgres"].put("memories", str(rowid), text)
        pg.commit()
        pg.execute("ANALYZE")
        indexing["posting/postgres"] = time.perf_counter() - t0

    by_freq = [t for (t,) in fts.execute("SELECT term FROM v ORDER BY doc DESC")]
    common = by_freq[0]
    rare = by_freq[len(by_freq) // 2]
    mix = [by_freq[3], by_freq[40], by_freq[len(by_freq) // 4], by_freq[len(by_freq) // 2]]
    held = dict(fts.execute("SELECT term, doc FROM v"))

    print(f"{args.docs} documents; indexing (s): "
          + ", ".join(f"{k} {v:.1f}" for k, v in indexing.items()))
    print(f"questions: rare {rare!r} ({held[rare]} docs), common {common!r} "
          f"({held[common]} docs), mix of 4 ({sum(held[w] for w in mix)} postings)")
    print(f"{'engine':<18}{'rare ms':>10}{'common ms':>12}{'mix ms':>10}")

    def fts_top(phrases):
        query = " OR ".join(f'"{p}"' for p in phrases)
        return fts.execute("SELECT rowid, bm25(m) FROM m WHERE m MATCH ? "
                           "ORDER BY bm25(m) LIMIT 20", (query,)).fetchall()

    print(f"{'fts5 top-20':<18}" + "".join(
        f"{timed(lambda q=q: fts_top(q)):>{w}.2f}"
        for q, w in (([rare], 10), ([common], 12), (mix, 10))))
    for name, index in engines.items():
        for q in ([rare], [common], mix):
            expected, got = fts5_bm25(fts, q), index.bm25("memories", q)
            assert set(expected) == set(got) and all(
                abs(expected[d] - got[d]) < 1e-9 for d in expected), (name, q)

        def top(q, index=index):
            scores = index.bm25("memories", q)
            return heapq.nsmallest(20, scores.items(), key=lambda kv: (kv[1], kv[0]))

        print(f"{name:<18}" + "".join(
            f"{timed(lambda q=q: top(q)):>{w}.2f}"
            for q, w in (([rare], 10), ([common], 12), (mix, 10))))


if __name__ == "__main__":
    main()
