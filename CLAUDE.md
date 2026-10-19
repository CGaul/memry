# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Memry is an open, self-hostable memory layer for AI agents: MCP-native,
local-first, published to PyPI as `memry`. One Python package
(`src/memry`) serves a Python API, the `memry` CLI, a REST API with an
inline dashboard, and MCP (stdio and streamable HTTP), all over one
SQLite store. `docs/architecture.md` is the detailed design record;
`README.md` is the user-facing reference.

AWM workflows file issues and PRs against the fork `CGaul/memry`
(`origin`); PRs to upstream `cosmin-novac/memry` are opened by hand.

## Commands

```sh
. .venv/bin/activate                         # the project venv (Python 3.12)
python -m pip install -e ".[dev]"            # install, as CI does
python -m pytest -q                          # gate 1: the test suite (CI job "test")
python deploy/release_check.py               # gate 2: release drift (CI job "check")
git config core.hooksPath deploy/git-hooks   # once per clone: run gate 2 on pre-push
memry serve --host 127.0.0.1 --port 8787     # REST + dashboard + /mcp; needs a text and a decision model
./run.sh --port-offset 100                   # isolated instance for review (scratch DB under .memry/)
```

The gates are the two CI jobs in `.github/workflows/publish.yml`: `python -m pytest -q` (the suite in `tests/`, about 40 s, which also parses the inline dashboard JS with `node --check`) and `python deploy/release_check.py` (release drift: any change under `src/memry`, `pyproject.toml`, `Dockerfile` or `requirements-docker.txt` needs a new `__version__` in `src/memry/__init__.py`, so such a change bumps the patch version, as every merged PR here does). There is no typecheck or lint gate.

## Architecture

`docs/architecture.md` §2 defines the layers. From the outside in:

| Layer | Where | Rule |
|---|---|---|
| Public surfaces | `cli.py`, `rest.py` (REST API, inline dashboard, `/mcp` mount), `mcp_server.py`, `enrichment.py` | Call `MemoryStore`; never implement memory behaviour themselves. |
| Facade | `store.py` (`MemoryStore`) | Ownership checks and the read/write workflows; the main invariant boundary. |
| Intelligence + retrieval | `intelligence/`, `retrieval.py`, `filters.py` | Derived outputs stay rebuildable from evidence; nothing forgets a memory for its age. |
| Providers | `providers/` (text LLMs, embeddings, the decision model) | Optional; the zero-key fallbacks (`NoneLLM`, `HashEmbedder`) keep working. |
| Persistence | `backends/` (`LocalBackend` on SQLite; `mem0_adapter.py` is comparison/import only) | SQLite is the only production source of truth. |
| Shared | `models.py`, `config.py` | Imported by every layer; they import nothing from the layers. |

Imports point inward only: surfaces → `store.py` → intelligence/retrieval →
providers and backends. Accounts and auth (`accounts.py`, `oauth.py`,
`principal.py`) sit beside the surfaces and keep login data in their own
SQLite file.

Standing exceptions visible today (they move as refactors land, so measure
against the current code rather than this list):
- `cli.py`, `rest.py` and `mcp_server.py` import from `intelligence/`
  (`when`, `context`, `split`) and `filters.py` directly, for request and date
  parsing, instead of going through `MemoryStore`.
- `providers/decisions.py` imports `intelligence.extraction.parse_lenient_json`
  (a function-level import, provider → intelligence).

The dashboard is one inline HTML/JS string in `rest.py`;
`tests/test_dashboard_js.py` parses it with `node --check`, so a stray brace
fails the suite. The version lives only in `src/memry/__init__.py`, and a push
to `main` with a new version is a release: CI tags it, publishes to PyPI and
creates the GitHub release. `tests/test_container_build.py` keeps
`requirements-docker.txt` in sync with `pyproject.toml`.

## AI Workflow Manager (AWM)

This repo is prepared for AWM: `.claude/skills/` holds the pipeline skills it
drives (`/plan-feature`, `/plan-bugfix`, `/plan-refactoring` →
`/design-implementation` → `/implement`).

Contracts worth knowing before editing those skills:

- Every stage reads and writes `.gh-work/<ticket>/` (gitignored). AWM detects
  step completion by **globbing that directory**, and its scan is **flat** —
  a file in a subdirectory is invisible to it.
- The plan skills emit a fixed section list plus an implementation-metadata
  block (`Target Branch`, `Branch originates from`, `Target branch on PR`,
  `Tenant`). Drift means AWM treats the ticket as unstructured and re-plans it.
- The branch prefixes `feature/`, `bugfix/`, `refactor/` are how AWM derives
  the workflow kind — they are not free-form.
- `/implement` must **reuse** the branch AWM already created for the workflow
  rather than cutting a new one.
- `run.sh` is the contract AWM's "Run app" button uses: it accepts
  `--port-offset N` / `--frontend-port P` / `--backend-port P` and announces
  its URLs on stdout as `[awm] url <label>=<url>`.

`.claude/awm-project.json` records the detected stack profile this setup was
rendered from. Re-running the AWM project setup re-renders from that file.

## graphify

This project has a knowledge graph at `graphify-out/` with god nodes, community
structure, and cross-file relationships.

Rules:
- **MATERIALIZE FIRST.** `graphify-out/graph.json` is a build artifact and is
  git-ignored — on a fresh checkout it will be missing. Before querying, run
  `graphify . --update`. It replays the committed semantic cache
  (`graphify-out/cache/semantic/`) for free and re-derives the AST locally, so
  this costs **zero LLM tokens** and needs no API key. Never re-extract from
  scratch just because the file is absent. (`graphify` must be installed:
  `pip install graphifyy`.)
- For codebase questions, run `graphify query "<question>"` first. Use
  `graphify path "<A>" "<B>"` for relationships and `graphify explain
  "<concept>"` for focused concepts — each returns a scoped subgraph, usually
  much smaller than `GRAPH_REPORT.md` or raw grep output.
- **Resolve before you traverse.** `graphify affected "<bare label>"` seeds
  from the first match of a tiered scan, so an ambiguous label yields a
  confidently wrong blast radius. Run `graphify explain "<label>"` first, check
  its `Source:` line, then pass the printed `ID:` to `affected`.
- Read `graphify-out/GRAPH_REPORT.md` only for broad architecture review, or
  when query/path/explain do not surface enough context. It is committed
  because it carries the community labels a rebuild cannot regenerate.
- **Tests are not in the graph** (`.graphifyignore` excludes them), so results
  are not dominated by scaffolding. The cost: `graphify affected` never lists
  the tests that exercise a symbol — grep those separately.

Known limits (do not mistake these for missing code):
- Runtime-string linkage (config keys, event names, route strings, message
  kinds) is not an AST edge, so those couplings are absent or weakly connected.
- Community **ids** are partitioner output and are not comparable across
  rebuilds that change membership.
