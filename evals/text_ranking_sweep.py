"""Sweeps of the text ranking's constants, one at a time, on the relative
retrieval benchmark's dense world (``relative_retrieval_benchmark.py``).

The constants live in ``memry/config.py``: ``retrieval.rrf_k``,
``vector_weight``, ``keyword_weight``, ``recency_weight``,
``importance_weight`` and ``decision.rerank_pool``. For an experiment a
person replaces each with ``MEMRY_RANK_<NAME>`` (read once, when the config
module is imported). This script runs the benchmark once for each setting,
as a subprocess, every other constant at today's value, in two modes: the
plain ranking (``hybrid``, links none), where the constants order the
results, and the linked search (``linked k1``, links measured), where they
choose its candidates. It prints a table per sweep and mode.

Run (the PhD notes, text-ranking-constants-plan):
    python evals/text_ranking_sweep.py --outdir OUT                # every sweep
    python evals/text_ranking_sweep.py --outdir OUT --sweeps RRF_K
    python evals/text_ranking_sweep.py --outdir OUT --picks RERANK_POOL=30 RERANK_POOL=40 \
        --bench --jev --per-family 25 --modes "linked jev" --links measured

Every run reads its vectors from the embedding cache only (``--cache-only``):
the run stops at a text the cache lacks, and nothing is embedded. Each run's
``--out`` JSON and log are kept in OUT under the setting's name; a run whose
JSON is there already is not run again.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from relative_retrieval_benchmark import RECALL  # noqa: E402

#: Today's values, as ``memry.config`` has them.
TODAY = {"RRF_K": "60", "VECTOR_WEIGHT": "1.0", "KEYWORD_WEIGHT": "1.0",
         "RECENCY_WEIGHT": "0.15", "IMPORTANCE_WEIGHT": "0.15", "RERANK_POOL": "20"}
#: The values each sweep tries (the plan), in the order run.
SWEEPS = {
    "RRF_K": ["20", "60", "120", "300"],
    "VECTOR_WEIGHT": ["0.5", "1.0", "1.5", "2.0"],
    "KEYWORD_WEIGHT": ["0.5", "1.0", "1.5", "2.0"],
    "RECENCY_WEIGHT": ["0", "0.15", "0.3"],
    "IMPORTANCE_WEIGHT": ["0", "0.15", "0.3"],
    "RERANK_POOL": ["20", "30", "40"],
}
BENCH = ["--world", "dense", "--owner", "--sizes", "2000", "--cache-only",
         "--modes", "hybrid", "linked k1", "--links", "none", "measured"]
MOVED = 0.02


def same(name: str, value: str) -> bool:
    """Whether ``value`` is today's value of ``name``."""
    return float(value) == float(TODAY[name])


def tag_of(setting: dict[str, str]) -> str:
    """The file name of a setting: "today" for none, else NAME=VALUE joined by commas."""
    return ",".join(f"{k}={v}" for k, v in sorted(setting.items())) or "today"


def command(setting: dict[str, str], out: pathlib.Path, bench: list[str]):
    """The benchmark's command line and environment for ``setting``."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("MEMRY_RANK_")}
    env.pop("OPENAI_API_KEY", None)  # --cache-only reads the cache; nothing is embedded
    args = [sys.executable, str(HERE / "relative_retrieval_benchmark.py"), *bench,
            "--out", str(out)]
    for name, value in setting.items():
        env[f"MEMRY_RANK_{name}"] = value
    return args, env


def run_all(settings: list[dict[str, str]], outdir: pathlib.Path, bench: list[str],
            jobs: int) -> None:
    """Run the benchmark for every setting whose JSON is not in ``outdir`` yet,
    ``jobs`` at a time."""
    todo = [s for s in settings if not (outdir / f"{tag_of(s)}.json").exists()]
    running: list[tuple[subprocess.Popen, pathlib.Path, str]] = []
    while todo or running:
        while todo and len(running) < jobs:
            setting = todo.pop(0)
            tag = tag_of(setting)
            log = outdir / f"{tag}.log"
            args, env = command(setting, outdir / f"{tag}.json", bench)
            with log.open("w") as handle:
                process = subprocess.Popen(args, env=env, stdout=handle,
                                           stderr=subprocess.STDOUT)
            running.append((process, log, tag))
            print(f"started {tag}", flush=True)
        time.sleep(5)
        for process, log, tag in list(running):
            if process.poll() is not None:
                running.remove((process, log, tag))
                status = "done" if process.returncode == 0 else f"failed ({process.returncode})"
                print(f"{status} {tag}", flush=True)
                if process.returncode != 0:
                    for other, _, _ in running:
                        other.kill()
                    sys.exit(f"stopped: {tag} failed; see {log}")


def runs(path: pathlib.Path) -> dict[str, dict[str, dict]]:
    """Each mode's per-family results in a run's JSON, by mode label."""
    data = json.loads(path.read_text())
    return {key.split("|")[-1]: {f: v for f, v in res.items() if not f.startswith("_")}
            for key, res in data.items()}


def summary(res: dict[str, dict]) -> dict[str, float | None]:
    """The benchmark's headline (recall@10 for the families in ``RECALL``, MRR
    for the rest) over the families but set, and over all of them as the
    link-weight finding gave it; mean MRR, recall@10 and r20 over the
    families but set; mean wrong first over those with near misses; set's
    recall@10, set@20 and set@40."""
    rest = {f: v for f, v in res.items() if f != "set"}
    mean = lambda xs: sum(xs) / len(xs)  # noqa: E731
    wrong = [v["wrong_first"] for v in res.values() if v["wrong_first"] is not None]
    out = {"headline": mean([v[RECALL.get(f, "mrr")] for f, v in rest.items()]),
           "headline25": mean([v[RECALL.get(f, "mrr")] for f, v in res.items()]),
           "mrr": mean([v["mrr"] for v in rest.values()]),
           "recall": mean([v["recall"] for v in rest.values()]),
           "r20": mean([v["r20"] for v in rest.values()]),
           "wrong_first": mean(wrong) if wrong else None}
    s = res.get("set", {})
    out.update({"set_recall": s.get("recall"), "set_at20": s.get("set_at20"),
                "set_at40": s.get("set_at40")})
    return out


def moved(res: dict[str, dict], base: dict[str, dict]) -> str:
    """The families whose headline moved by ``MOVED`` or more against ``base``."""
    out = []
    for family, v in res.items():
        key = RECALL.get(family, "mrr")
        before, after = base[family][key], v[key]
        if abs(after - before) >= MOVED - 1e-9:
            out.append(f"{family} {before:.2f} to {after:.2f}")
    return "; ".join(out) or "none"


def fmt(x: float | None, places: int = 3) -> str:
    return "-" if x is None else f"{x:.{places}f}"


def table(title: str, mode: str, rows: list[tuple[str, pathlib.Path]],
          base: pathlib.Path) -> str:
    """A markdown table of ``mode``, one row per (label, run JSON), against ``base``."""
    base_res = runs(base)[mode]
    lines = [f"### {title}, {mode}", "",
             "| value | headline (24, set apart) | headline (25) | mean MRR | mean recall@10 "
             "| mean r20 | mean wrong first | set recall@10 | set@20 | set@40 "
             "| families moved by 0.02 or more |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for label, path in rows:
        res = runs(path)[mode]
        s = summary(res)
        lines.append(f"| {label} | {fmt(s['headline'])} | {fmt(s['headline25'])} | "
                     f"{fmt(s['mrr'])} | {fmt(s['recall'])} | {fmt(s['r20'])} | "
                     f"{fmt(s['wrong_first'])} | {fmt(s['set_recall'], 2)} | "
                     f"{fmt(s['set_at20'], 2)} | {fmt(s['set_at40'], 2)} | "
                     f"{moved(res, base_res)} |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--sweeps", nargs="*", default=None, choices=list(SWEEPS))
    parser.add_argument("--picks", nargs="*", default=None, metavar="NAME=VALUE",
                        help="instead of sweeps: today's constants and each of these "
                             "settings (NAME=VALUE, several joined by commas)")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--bench", nargs=argparse.REMAINDER, default=[],
                        help="more benchmark arguments, after the sweep's own; "
                             "--modes and --links replace the sweep's; last on the line")
    args = parser.parse_args()
    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    bench = list(BENCH)
    for flag in ("--modes", "--links"):
        if flag in args.bench:
            i = bench.index(flag)
            j = i + 1
            while j < len(bench) and not bench[j].startswith("--"):
                j += 1
            del bench[i:j]
    bench += args.bench
    base = outdir / "today.json"
    if args.picks is not None:
        settings = [dict(p.split("=", 1) for p in pick.split(",")) for pick in args.picks]
        for s in settings:
            unknown = sorted(set(s) - set(TODAY))
            if unknown:
                parser.error(f"unknown {', '.join(unknown)}; the names are {', '.join(TODAY)}")
        run_all([{}, *settings], outdir, bench, args.jobs)
        rows = [("today", base)] + [(tag_of(s), outdir / f"{tag_of(s)}.json") for s in settings]
        text = "\n\n".join(table("today against the picks", mode, rows, base)
                           for mode in runs(base))
    else:
        names = args.sweeps or list(SWEEPS)
        settings = [{}]
        for name in names:
            settings += [{name: v} for v in SWEEPS[name] if not same(name, v)]
        run_all(settings, outdir, bench, args.jobs)
        parts = []
        for mode in runs(base):
            for name in names:
                rows = [(f"**{v}** (today)" if same(name, v) else v,
                         base if same(name, v) else outdir / f"{tag_of({name: v})}.json")
                        for v in SWEEPS[name]]
                parts.append(table(name, mode, rows, base))
        text = "\n\n".join(parts)
    print(text, flush=True)
    name = "picks" if args.picks is not None else "sweeps_" + "_".join(args.sweeps or ["all"])
    (outdir / f"{name}.md").write_text(text + "\n")


if __name__ == "__main__":
    main()
