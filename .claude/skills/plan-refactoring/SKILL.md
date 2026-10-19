---
name: plan-refactoring
description: Interactive interview that produces a well-structured GitHub refactoring ticket (behaviour-preserving redesign with a machine-checkable behavior-preservation contract) — the refactor entry point ahead of /design-implementation. Drives the codebase toward its target layering, grounded in the graphify knowledge graph.
disable-model-invocation: false
argument-hint: "[optional short refactoring topic]"
---

# Plan GitHub Refactoring $ARGUMENTS

Drive a **disciplined interview** with the user, then create a GitHub
**refactoring** ticket in a fixed structured format that
`/design-implementation`, `/implement`, and the AWM orchestrator itself parse
deterministically (AWM pins the exact section list below — drift means it
treats the ticket as unstructured and re-plans it). It is the
refactor sibling of `/plan-feature` and `/plan-bugfix` and runs **inline** — the
user must see and refine each section verbatim; never run it in a fork.

The repo is `CGaul/memry`, a single Python package (`src/memry`, Python ≥3.11, built with hatchling) that serves a CLI, a REST API with an inline dashboard, and MCP over Starlette/uvicorn, with SQLite as its only production store. The gates are the two CI jobs in `.github/workflows/publish.yml`: `python -m pytest -q` (the suite in `tests/`, about 40 s, which also parses the inline dashboard JS with `node --check`) and `python deploy/release_check.py` (release drift: any change under `src/memry`, `pyproject.toml`, `Dockerfile` or `requirements-docker.txt` needs a new `__version__` in `src/memry/__init__.py`, so such a change bumps the patch version, as every merged PR here does). There is no typecheck or lint gate.
Those gates plus the contract captures below are the verification currency.

## Outputs and completion

- Creates one GitHub issue (label `refactor` when it exists — never a priority
  or kind label), then writes `.gh-work/<n>/issue.json` — the completion signal
  AWM binds the workflow to — and `.gh-work/<n>/agent_context.md`.
- Fetch `issue.json` only after the issue number is stamped into the Target
  Branch line (Step 8): AWM reads the branch from it.
- The human signs off on the rendered draft before any GitHub write. This is a
  human-in-the-loop interview: ask, then wait for the answer.
- Hard rules: behaviour is frozen (a behaviour change is a separate ticket);
  every smell is anchored to code; every contract capture has run green once
  during the interview; open questions never reach the ticket; the ticket
  carries no Priority; a redesign that deepens a layering violation needs a
  recorded justification in Alternatives Considered.

A refactoring is a **behaviour-preserving redesign** (SOLID, DRY, complexity
reduction via composition), up to and including preparing an in-repo
**separation line**. Nothing observable may change, so there is no User Story,
no "Why?" persona and no root cause: the ticket centres on the **design-level
end state, the concrete smells being removed, and a behavior-preservation
contract**. The `refactor/` Target-Branch prefix is the refactor signal
downstream (AWM derives the workflow kind from it).

## Architectural direction — standing context for every refactor

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

During the interview, classify each smell by the layer boundary it violates
and prefer the redesign that carves a seam along that boundary. Every
refactor must leave the code **closer to this layering, never further** —
even when its scope is a single module. Standing violations move as refactors
land, so measure them against the current code (graphify, then grep for the
outward imports) instead of trusting a recorded list.

## Preamble — resolve paths once

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
WORK_DIR="$REPO_ROOT/.gh-work/draft-refactor"   # not /tmp: it may not survive a long interview
mkdir -p "$WORK_DIR"
```

The issue doesn't exist yet, so drafts live in this gitignored draft dir;
Step 8 creates the real `.gh-work/<n>/`.

## Operating principles — apply to the whole interview

- **Don't assume. Don't hide confusion. Surface tradeoffs.** When two
  redesigns exist, present both. Push back when warranted.
- **Behaviour is frozen.** If the user describes a behaviour change ("and also
  fix the stuck step"), stop: that is a bug or feature and belongs in its own
  ticket via `/plan-bugfix` or `/plan-feature`. A refactor that discovers a
  real bug **reports** it, never fixes it here.
- **Anchor smells with graphify.** Refuse a vague "clean up the code". Every
  smell names a file/class/function, grounded read-only in the knowledge graph:
  ```bash
  graphify explain "<concept>"          # first: check its Source: line, note its ID:
  graphify affected "<ID from explain>" # blast radius — seed with the ID, never a bare label
  graphify query "<question about the smell's blast radius>"
  graphify path "<A>" "<B>"
  ```
  Resolve before you traverse: a bare label seeds `affected` from the first
  match of a tiered scan, and a label that exists in several files (a wrapper
  keeps the wrapped method's name) yields a confidently wrong blast radius.
  Respect the graph's other limits too (see CLAUDE.md): a barrel/re-export
  module makes symbol-level edges dangle — **grep the symbol** to confirm the
  real blast radius — and runtime-string couplings (config keys, event names,
  route strings, message kinds) are invisible to the AST — grep those strings
  explicitly whenever a smell touches them.
- **The contract is mandatory and machine-checkable.** Every capture command
  in the Behavior Preservation Contract must be **executed once during the
  interview** (read-only) to prove it runs green and produces stable output —
  the implement stage re-runs the same commands before and after the refactor
  and fails the PR on any drift.
- **Two artifacts, one source of truth.** The GitHub ticket is the
  structured **source of truth** (AWM parses the *ticket*); `agent_context.md`
  only ADDS depth for the downstream stages — no ticket section may say "see
  agent context".
- **Draft, then confirm.** You draft every section yourself from the topic and
  the graph; the user corrects. Mark everything you inferred rather than heard
  with `_(inferred)_`, and show the drafted sections verbatim. Discrete choices
  (redesign A vs B, which surfaces to capture, approve vs change) go through
  AskUserQuestion — AWM can answer those from its UI; open questions stay free
  text.

## Step 1 — Scope opener

Read `$ARGUMENTS` as the starting topic. If it already says what is
structurally wrong and roughly what the end state is, use it; otherwise ask
for that, at the level of modules and responsibilities. No "why it matters"
needed.

## Step 2 — Ground and draft every section

Ground the smells in the graph and the code first, then draft:

1. **Refactoring Goal** — the design-level end state in one short paragraph
   (e.g. "the order module stops importing the HTTP client: payment calls
   reach it through a port injected at construction"). Name which layer(s) the
   end state establishes. Stay at module boundaries and responsibilities —
   the design stage produces the ordered change plan — and use no wording
   that implies a behaviour change.
2. **Smell Inventory** — each smell anchored to a file/class/function, with
   graphify grounding quoted (god-node degree, community spread, blast
   radius) plus grep confirmation where the graph's limits apply. Note the
   onion-layer violation each smell represents.
3. **Scope** — the module/file list the refactor may touch — and **Out of
   Scope** (its own section in the ticket): the **hard downstream boundary**.
   The implement stage treats anything listed there as off-limits, so be
   explicit about adjacent code that must NOT be reshaped.
4. **Behavior Preservation Contract** — **required**, fixed format (a free
   paragraph will not do):
   ```markdown
   ## Behavior Preservation Contract
   * Surface: <name> | capture: `<read-only shell command, run at repo root>`
   * Surface: <name> | capture: `<read-only shell command, run at repo root>`
   * Gate: `python -m pytest -q` and `python deploy/release_check.py` green
   All listed captures must produce identical output before and after the refactor.
   ```
   Pick **2–6 Surface lines** the refactor could plausibly disturb; run each
   command now and refine it until it runs green and its output is
   deterministic (sort it). This repo's capture menu:

   Run with the project venv active (`. .venv/bin/activate`); every command
   below is read-only, builds its store in `:memory:`, and has run green twice
   with identical output.

   - **REST + dashboard routes** (path and methods):
     `python -c "from memry.config import Config; from memry.store import MemoryStore; from memry.providers.llm import NoneLLM; from memry.providers.embeddings import HashEmbedder; from memry.rest import create_app; app = create_app(MemoryStore(Config(db_path=':memory:'), llm=NoneLLM(), embedder=HashEmbedder(64))); print(*sorted(getattr(r, 'path', '?') + ' ' + ','.join(sorted(getattr(r, 'methods', None) or ['*'])) for r in app.routes), sep='\n')"`
   - **MCP tool names**:
     `python -c "import asyncio; from memry.config import Config; from memry.store import MemoryStore; from memry.providers.llm import NoneLLM; from memry.providers.embeddings import HashEmbedder; from memry.mcp_server import create_server; s = create_server(MemoryStore(Config(db_path=':memory:'), llm=NoneLLM(), embedder=HashEmbedder(64))); print(*sorted(t.name for t in asyncio.run(s.list_tools())), sep='\n')"`
   - **SQLite schema** (every table, index and trigger a fresh store creates):
     `python -c "from memry.config import Config; from memry.store import MemoryStore; from memry.providers.llm import NoneLLM; from memry.providers.embeddings import HashEmbedder; db = MemoryStore(Config(db_path=':memory:'), llm=NoneLLM(), embedder=HashEmbedder(64)).backend._db; print(*(r[0] for r in db.execute('SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name')), sep='\n')"`
   - **`MemoryStore` public API** (method names with signatures):
     `python -c "import inspect; from memry.store import MemoryStore; print(*sorted(n + str(inspect.signature(f)) for n, f in inspect.getmembers(MemoryStore, inspect.isfunction) if not n.startswith('_')), sep='\n')"`
   - **CLI surface**: `python -m memry.cli --help` (add `<subcommand> --help`
     for each subcommand the refactor touches)
   - **Configuration keys**: `grep -ho "MEMRY_[A-Z_]*[A-Z]" src/memry/*.py | sort -u`

   The rule is always: **same command, both sides, empty diff.** For a
   UI-only refactor with no greppable surface, state the visual invariant in
   prose here and carry it into Acceptance Criteria as a manual check
   — this repo has no screenshot-parity harness.
5. **Assumptions** — standing assumptions (e.g. "no live workflows depend on
   the internal shape being moved; the DB is touched by migrations only").
6. **Acceptance Criteria** — no "make it cleaner". Checkbox bullets
   (`- [ ]`), at minimum:
   - `python -m pytest -q` and `python deploy/release_check.py` green;
   - every Behavior Preservation Contract capture identical before vs after,
     with both outputs quoted in the PR;
   - no public behaviour change on the touched surface (same
     signatures/routes/messages, or every removed public API names its
     replacement);
   - **when a Separation Line applies**: the one-way import check below
     returns nothing.
7. **Alternatives Considered** — at least one alternative redesign (or "leave
   as is") plus why it was rejected.
8. **Separation Line** *(optional)* — only when this refactor prepares an
   in-repo architectural boundary; otherwise the section is the single word
   `None`. Fixed format, with workspace-relative roots:
   ```markdown
   ## Separation Line
   * Stays: <name> (root: <workspace-relative path>)
   * Moves: <name> (root: <workspace-relative path>)
   Mode: in-repo boundary — moved code relocates under the moves-root; actual package extraction is a later mechanical step.
   * Moves: <path or module> [symbols: A, B | all]
   Imports flow one way: code under the moves-root must not import from the stays-root.
   Check: `grep -RnE "from '.*<stays-root fragment>" <moves-root>` returns nothing.
   ```
9. **Metadata values** — branch slug (short, hyphenated; the issue number is
   stamped in Step 8), branch originates from (default `main`), target branch
   on PR (default `main`), relates to (links or "—"). Blockers go into
   "Relates to" as `blocked by #NNN`. No Priority.

## Step 3 — Confirm in two batches

- **Batch 1** — Refactoring Goal, Smell Inventory, Scope + Out of Scope, and
  the Behavior Preservation Contract (with each capture's output), together
  with the open questions and any redesign choice (AskUserQuestion).
- **Batch 2** — Assumptions, Acceptance Criteria, Alternatives Considered,
  Separation Line and the metadata values.

Show each batch verbatim, ask, and fold the answers in. Behaviour changes the
user raises go to a separate ticket (see the principles).

## Step 4 — Render the ticket and the agent context

The `## Implementation metadata` block is **always the first section**, in
exactly this shape (NNN is stamped in Step 8):

```markdown
## Implementation metadata
* Target Branch: refactor/NNN_<branch-slug>
* Branch originates from: <base>
* Target branch on PR: <pr-base>
* Tenant: all
* Relates to: <links or "—">
```

The `* Tenant:` line is **required by AWM's ticket parser** and must hold a
real literal — `—`/`n/a`/`tbd` are rejected as placeholders, which would make
AWM treat the whole ticket as unstructured and re-plan it.

Then the authored sections below it, using **exactly these headings in this
order** (pinned in `REFACTORING_SECTIONS`): `## Refactoring Goal`,
`## Smell Inventory`, `## Scope`, `## Behavior Preservation Contract`,
`## Assumptions`, `## Acceptance Criteria`, `## Out of Scope`,
`## Alternatives Considered` — plus the optional `## Separation Line` last.
Write it all to `$WORK_DIR/ticket_body.md`.

Write `$WORK_DIR/agent_context.md` alongside it — agent-facing, never posted
to GitHub. It starts with:

```markdown
# Agent context — ticket #<id>
_Agent-facing planning context — not posted to GitHub._
```

Then four sections: **Interview detail** (load-bearing user statements
verbatim), **Grounding evidence** (the graphify output and grep confirmations,
plus each contract capture's interview-time output — the "before" reference),
**Rejected alternatives** (each with a one-line rationale), **Resolved open
questions / constraints**. Match its length to what the design and implement
stages need: the evidence and decisions, no filler and no restated summaries.

## Step 5 — Template check (what `issuePlanState` applies)

AWM's `issuePlanState` decides whether the ticket is structured (the same
rules as the plan skills' template validators). Check `ticket_body.md` against exactly what it applies, and fix
anything that fails:

- Sections are split on lines matching `^## <heading>`; every heading in
  `REFACTORING_SECTIONS` must be present and its content non-empty. The parser
  does not skip fenced code blocks, so no line inside a fence may start with
  `## ` (the contract and Separation Line formats above are templates — the
  ticket carries them as real sections, not as fenced examples).
- In `## Implementation metadata`, each of `Target Branch`,
  `Branch originates from`, `Target branch on PR` and `Tenant` is a line
  `* <Field>: <value>` (or `- `), and no value is empty, wrapped in `<…>` or
  `[…]`, or one of `tbd`, `todo`, `n/a`, `—`, `-`.
- `## Acceptance Criteria` contains at least one line `- [ ] <text>` (a `*`
  bullet does not count).
- The Target Branch starts with `refactor/` (that is the kind AWM derives),
  and after Step 8 it no longer contains `NNN`.

Plus this skill's format rules: headings in the order above, every
`* Surface:` line in the fixed format with a capture that ran green, and the
Separation Line `None` or complete.

## Step 6 — User sign-off

Show the rendered `ticket_body.md` and ask for approval (AskUserQuestion:
approve / request changes). On change requests, revise and re-run Step 5. Do
not create the issue without an explicit approval.

## Step 7 — Create the issue

Derive a concise title (`refactor: <summary>` recommended). Then:

```bash
command gh issue create --repo CGaul/memry \
  --title "<title>" --body-file "$WORK_DIR/ticket_body.md" --label refactor 2>/dev/null \
  || command gh issue create --repo CGaul/memry \
       --title "<title>" --body-file "$WORK_DIR/ticket_body.md"
```

(The fallback covers a missing `refactor` label — never invent labels, and
never add priority/kind labels: the `refactor/` branch prefix is the signal.)

## Step 8 — Stamp the number + write the AWM bind artifacts

Extract the issue number `<n>` from the returned URL, replace `NNN` in the
Target Branch line, and update the issue **before** fetching `issue.json` —
AWM reads the Target Branch out of `issue.json` to rename the workflow branch,
so a pre-edit fetch would carry the placeholder:

```bash
command gh issue edit <n> --repo CGaul/memry --body-file "$WORK_DIR/ticket_body.md"
mkdir -p "$REPO_ROOT/.gh-work/<n>"
command gh api repos/CGaul/memry/issues/<n> > "$REPO_ROOT/.gh-work/<n>/issue.json"
# Stamp the real issue number into agent_context.md's header, then persist it
# next to issue.json (it is NOT posted to GitHub).
cp "$WORK_DIR/agent_context.md" "$REPO_ROOT/.gh-work/<n>/agent_context.md"
```

## Step 9 — Report

Print the issue URL and the next steps: `/design-implementation <n>
--refactoring`, then `/implement <n> --refactoring` (under AWM both run
automatically, with a human design-review gate on the diagrams in between).

## AWM integration (best-effort, never blocking)

Only when `AWM_WORKFLOW_ID` is set; otherwise skip — the Step 8 `issue.json`
write is the authoritative bind signal. Record lineage **once**, at the very
end: load the tool via `ToolSearch("select:mcp__awm-lineage__record_step")`
(fall back to `"+record_step"`), then call it once:

```json
{"ticket_id": <the issue number, as an integer — NOT the workflow id>,
 "step": "<the value of the AWM_STEP env var — 'plan_ticket' for this stage>",
 "status": "succeeded",
 "summary": "<one line: the refactor ticket was created and persisted>",
 "detail": {"artifacts": ["ticket_body.md", "issue.json", "agent_context.md"]}}
```

If the env var is absent, the tool cannot load, or the call errors: note it
briefly and continue — retry at most once, never block on it.
