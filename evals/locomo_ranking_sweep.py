"""The text ranking's constants on LoCoMo: how often Memry, searching with no
judge, returns a memory that comes from a question's evidence turns, on
copies of the third LoCoMo run's stores (PhD notes,
text-ranking-constants-plan).

Every question of categories 1 to 4 (1,540) is asked through
``MemoryStore.search`` (user "bench", limit 20, no evidence turns), with no
decision provider, so nothing is judged and no answer is written. A row is
scored as ``external_benchmarks.ask`` scores it: recall@5, @10 and @20 of the
evidence turns (``evidence_recall``) and the reciprocal rank of the first
memory that comes from one (``reciprocal_rank``). Over the search's order
before its limit (``MemoryStore._final_order``) each row also has pool@20,
@30 and @40: the share of the evidence turns some memory among the first 20,
30 or 40 comes from: the most a judged pool of that size can contain.

A memory's turns are those the run traced it to (``Ingested.turns_of``),
read from the rows of the run's results file, which lists them for every
memory it returned; a memory no row lists is traced by its own record (the
session of the save that made it, and the sessions of its source episodes).

The query vectors come from a copy of the run's embedding cache. A text the
cache lacks is not embedded: the search goes on without that vector
(``MemoryStore._query_vector`` reads the words alone) and the text is
counted under "misses".

The constants are read from ``MEMRY_RANK_<NAME>`` (``memry.config``). With
``--clock reference`` every search runs as if asked on the question's
reference date (LoCoMo's last session), so that recency counts; by default
the clock is today's, which makes every memory of 2023 equally old.

    python evals/locomo_ranking_sweep.py --one OUT.json [--clock reference]
    python evals/locomo_ranking_sweep.py --outdir OUT [--sweeps RRF_K ...] [--jobs 2]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import json
import os
import pathlib
import random
import shutil
import statistics
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE.parent))

SCRATCH = pathlib.Path(
    "/tmp/claude-0/-home-user-memry/996a9793-3f28-575b-82f4-2002793a03b2/scratchpad")
STORES = SCRATCH / "locomo_run/full3/stores"
CACHE = SCRATCH / "locomo/cache/embeddings_openai_text-embedding-3-small.sqlite"
DATASET = SCRATCH / "locomo/locomo10.json"
RUN = pathlib.Path("/home/user/phd/papers/memry-field-studies/data/locomo/third_run/"
                   "locomo_full.json.gz")
WORK = SCRATCH / "trc/locomo_work"
CATEGORIES = (1, 2, 3, 4)
LIMIT = 20
POOLS = (20, 30, 40)

TODAY = {"RRF_K": "60", "VECTOR_WEIGHT": "1.0", "KEYWORD_WEIGHT": "1.0",
         "RECENCY_WEIGHT": "0.15", "IMPORTANCE_WEIGHT": "0.15", "RERANK_POOL": "20"}
SWEEPS = {
    "RRF_K": ["20", "60", "120", "300"],
    "VECTOR_WEIGHT": ["0.5", "1.0", "1.5", "2.0"],
    "KEYWORD_WEIGHT": ["0.5", "1.0", "1.5", "2.0"],
    "RECENCY_WEIGHT": ["0", "0.15", "0.3"],
    "IMPORTANCE_WEIGHT": ["0", "0.15", "0.3"],
    "RERANK_POOL": ["20", "30", "40"],
}
#: The sweeps run a second time with the clock at the reference date.
CLOCKED = ("RECENCY_WEIGHT",)
METRICS = ("recall@5", "recall@10", "recall@20", "mrr", "pool@20", "pool@30", "pool@40")


# --------------------------------------------------------------------------
# one setting


def run_one(out: pathlib.Path, clock: str) -> None:
    from evals import external_benchmarks as xb
    import memry.retrieval as retrieval
    from memry.config import Config
    from memry.providers.embeddings import Embedder
    from memry.providers.llm import NoneLLM
    from memry.store import MemoryStore

    class NotCached(Exception):
        pass

    class CacheOnly(Embedder):
        """The run's embedder as the cache knows it; a text the cache lacks
        is counted and refused, never embedded."""

        name, _model, dimensions = "openai", "text-embedding-3-small", 1536

        def __init__(self) -> None:
            self.misses: list[str] = []

        def embed(self, texts: list[str]) -> list[list[float]]:
            self.misses.extend(texts)
            raise NotCached(f"{len(texts)} texts not cached")

    real = retrieval.datetime

    class Clock(real):
        at: _dt.datetime | None = None

        @classmethod
        def now(cls, tz=None):
            return cls.at if cls.at is not None else real.now(tz)

    if clock == "reference":
        retrieval.datetime = Clock

    rows_run = json.loads(gzip.open(RUN).read())["rows"]
    traced: dict[tuple[str, str], set[str]] = {}
    for row in rows_run:
        for m in row["memories"]:
            traced.setdefault((row["conversation"], m["id"]), set()).update(m["turns"])

    base = CacheOnly()
    work = out.parent / (out.stem + ".work")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    cache = work / "embeddings.sqlite"
    shutil.copy(CACHE, cache)
    embedder = xb.SqliteEmbeddingCache(base, cache)
    rows, fallback, misses_by_q = [], 0, 0
    config_seen = None
    started = time.time()
    for conv in xb.load_locomo(DATASET):
        db_path = work / f"{conv.conv_id}.sqlite"
        shutil.copy(STORES / f"{conv.conv_id}.sqlite", db_path)
        store = MemoryStore(Config(db_path=str(db_path)), llm=NoneLLM(), embedder=embedder)
        assert store.decider is None or not getattr(store.decider, "available", False)
        cfg = store.config
        config_seen = {"rrf_k": cfg.retrieval.rrf_k, "vector_weight": cfg.retrieval.vector_weight,
                       "keyword_weight": cfg.retrieval.keyword_weight,
                       "recency_weight": cfg.retrieval.recency_weight,
                       "importance_weight": cfg.retrieval.importance_weight,
                       "fused_weight": cfg.retrieval.fused_weight,
                       "rerank_pool": cfg.decision.rerank_pool,
                       "relevance_mode": store.relevance_mode()}
        session_of = {t.key: s.session_id for s in conv.sessions for t in s.turns}
        turns_of_session: dict[str, list[str]] = {}
        for s in conv.sessions:
            turns_of_session[s.session_id] = [t.key for t in s.turns]
        episode_session = {r["id"]: r["run_id"] for r in
                           store.backend._db.execute("SELECT id, run_id FROM episodes")}
        cache_turns: dict[str, set[str]] = {}

        def turns_of(memory) -> set[str]:
            nonlocal fallback
            key = (conv.conv_id, memory.id)
            if key in traced:
                return traced[key]
            if memory.id not in cache_turns:
                fallback += 1
                own = set((memory.metadata or {}).get("bench", {}).get("turns", []))
                for e in memory.source_episode_ids:
                    own.update(turns_of_session.get(episode_session.get(e), []))
                cache_turns[memory.id] = own
            return cache_turns[memory.id]

        seen: dict[str, list] = {}
        inner = store._final_order

        def capture(*args, **kwargs):
            seen["ranked"] = inner(*args, **kwargs)
            return seen["ranked"]

        store._final_order = capture
        for q in conv.questions:
            if q.category not in CATEGORIES:
                continue
            if clock == "reference":
                Clock.at = _dt.datetime.fromisoformat(q.reference_date)
                if Clock.at.tzinfo is None:
                    Clock.at = Clock.at.replace(tzinfo=_dt.timezone.utc)
            before = len(base.misses)
            seen.clear()
            results = store.search(q.question, user_id=xb.BENCH_USER, limit=LIMIT,
                                   evidence=False)
            missed = len(base.misses) - before
            misses_by_q += bool(missed)
            units = [turns_of(r.memory) for r in results[:LIMIT]]
            ranked = [turns_of(r.memory) for r in seen.get("ranked", results)]
            row = {"conversation": conv.conv_id, "qid": q.qid, "category": q.category,
                   "missed": missed, "linked": any("about" in r.signals for r in results),
                   "evidence_ranks": [k for k, got in enumerate(units, 1)
                                      if got & set(q.evidence)][:10],
                   "memories": [r.memory.id for r in results[:LIMIT]]}
            for at in xb.KS:
                row[f"recall@{at}"] = xb.evidence_recall(units, q.evidence, at)
            row["mrr"] = xb.reciprocal_rank(units, q.evidence)
            for at in POOLS:
                row[f"pool@{at}"] = xb.evidence_recall(ranked, q.evidence, at)
            rows.append(row)
        del store._final_order
        store.close()
        print(conv.conv_id, len(rows), f"{time.time() - started:.0f}s", flush=True)
    embedder.release()
    shutil.rmtree(work)
    out.write_text(json.dumps({
        "config": config_seen, "clock": clock,
        "env": {k: v for k, v in os.environ.items() if k.startswith("MEMRY_RANK_")},
        "questions": len(rows), "misses": len(base.misses),
        "questions_with_a_miss": misses_by_q, "missed_texts": base.misses[:50],
        "memories_traced_by_record": fallback, "seconds": round(time.time() - started, 1),
        "rows": rows}))


# --------------------------------------------------------------------------
# the sweep


def same(name: str, value: str) -> bool:
    return float(value) == float(TODAY[name])


def tag_of(setting: dict[str, str], clock: str = "today") -> str:
    tag = ",".join(f"{k}={v}" for k, v in sorted(setting.items())) or "today"
    return tag + ("" if clock == "today" else "@reference")


def run_all(jobs_list: list[tuple[dict[str, str], str]], outdir: pathlib.Path,
            jobs: int) -> None:
    todo = [(s, c) for s, c in jobs_list if not (outdir / f"{tag_of(s, c)}.json").exists()]
    running = []
    while todo or running:
        while todo and len(running) < jobs:
            setting, clock = todo.pop(0)
            tag = tag_of(setting, clock)
            env = {k: v for k, v in os.environ.items() if not k.startswith("MEMRY_RANK_")}
            env.pop("OPENAI_API_KEY", None)
            env.update({f"MEMRY_RANK_{k}": v for k, v in setting.items()})
            log = (outdir / f"{tag}.log").open("w")
            process = subprocess.Popen(
                [sys.executable, __file__, "--one", str(outdir / f"{tag}.json"),
                 "--clock", clock], env=env, stdout=log, stderr=subprocess.STDOUT)
            running.append((process, tag))
            print(f"started {tag}", flush=True)
        time.sleep(5)
        for process, tag in list(running):
            if process.poll() is not None:
                running.remove((process, tag))
                print(f"{'done' if process.returncode == 0 else 'FAILED'} {tag}", flush=True)
                if process.returncode != 0:
                    for other, _ in running:
                        other.kill()
                    sys.exit(f"stopped: {tag} failed; see {outdir / (tag + '.log')}")


def means(rows: list[dict], category: int | None = None) -> dict[str, float]:
    kept = [r for r in rows if category is None or r["category"] == category]
    out = {}
    for m in METRICS:
        vals = [r[m] for r in kept if r[m] is not None]
        out[m] = statistics.mean(vals) if vals else None
    out["n"] = sum(r["recall@10"] is not None for r in kept)
    return out


def paired(rows: list[dict], base: list[dict], metric: str = "recall@10",
           resamples: int = 10_000, seed: int = 7) -> tuple[float, float, float, int, int]:
    """Mean difference of ``metric`` against ``base`` over the questions both
    score, its 95% interval over questions resampled, and the questions that
    rose and fell."""
    b = {r["qid"]: r[metric] for r in base}
    diffs = [r[metric] - b[r["qid"]] for r in rows
             if r[metric] is not None and b.get(r["qid"]) is not None]
    rnd = random.Random(seed)
    n = len(diffs)
    boot = sorted(sum(diffs[rnd.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    return (sum(diffs) / n, boot[int(0.025 * resamples)], boot[int(0.975 * resamples) - 1],
            sum(d > 0 for d in diffs), sum(d < 0 for d in diffs))


def load_rows(path: pathlib.Path) -> list[dict]:
    return json.loads(path.read_text())["rows"]


def table(title: str, rows: list[tuple[str, pathlib.Path]], base: pathlib.Path) -> str:
    base_rows = load_rows(base)
    lines = [f"### {title}", "",
             "| value | recall@5 | recall@10 | recall@20 | MRR | pool@30 | pool@40 "
             "| recall@10 against today (95% interval) | rose / fell | misses |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for label, path in rows:
        data = json.loads(path.read_text())
        m = means(data["rows"])
        d, lo, hi, up, down = paired(data["rows"], base_rows)
        lines.append(f"| {label} | {m['recall@5']:.3f} | {m['recall@10']:.3f} | "
                     f"{m['recall@20']:.3f} | {m['mrr']:.3f} | {m['pool@30']:.3f} | "
                     f"{m['pool@40']:.3f} | {d:+.4f} ({lo:+.4f} to {hi:+.4f}) | "
                     f"{up} / {down} | {data['misses']} |")
    return "\n".join(lines)


def category_table(title: str, rows: list[tuple[str, pathlib.Path]]) -> str:
    lines = [f"### {title}, by category (recall@10 / MRR)", "",
             "| value | " + " | ".join(f"{c} {n}" for c, n in
                                       ((1, "multi-hop"), (2, "temporal"), (3, "open-domain"),
                                        (4, "single-hop"))) + " |",
             "|---|---|---|---|---|"]
    for label, path in rows:
        data = load_rows(path)
        cells = []
        for c in CATEGORIES:
            m = means(data, c)
            cells.append(f"{m['recall@10']:.3f} / {m['mrr']:.3f} (n {m['n']})")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--one", default=None, help="run one setting (the environment's)")
    parser.add_argument("--clock", choices=["today", "reference"], default="today")
    parser.add_argument("--outdir", default=None)
    parser.add_argument("--sweeps", nargs="*", default=None, choices=list(SWEEPS))
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    if args.one:
        run_one(pathlib.Path(args.one), args.clock)
        return
    if not args.outdir:
        parser.error("--one or --outdir")
    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    names = args.sweeps or list(SWEEPS)
    todo: list[tuple[dict[str, str], str]] = [({}, "today")]
    if any(n in CLOCKED for n in names):
        todo.append(({}, "reference"))
    for name in names:
        for v in SWEEPS[name]:
            if not same(name, v):
                todo.append(({name: v}, "today"))
                if name in CLOCKED:
                    todo.append(({name: v}, "reference"))
    run_all(todo, outdir, args.jobs)
    parts = []
    for name in names:
        for clock in ("today",) + (("reference",) if name in CLOCKED else ()):
            base = outdir / f"{tag_of({}, clock)}.json"
            rows = [(f"**{v}** (today)" if same(name, v) else v,
                     base if same(name, v) else outdir / f"{tag_of({name: v}, clock)}.json")
                    for v in SWEEPS[name]]
            title = name + ("" if clock == "today" else ", clock at the reference date")
            parts.append(table(title, rows, base))
            parts.append(category_table(title, rows))
    text = "\n\n".join(parts)
    print(text)
    (outdir / f"sweeps_{'_'.join(args.sweeps or ['all'])}.md").write_text(text + "\n")


if __name__ == "__main__":
    main()
