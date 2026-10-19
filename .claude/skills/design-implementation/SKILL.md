---
name: design-implementation
description: Architecture-aware design stage for a GitHub feature, bugfix or refactoring issue — produces the architectural analysis + implementation plan, checks them in one validation pass, renders an affected-modules mermaid diagram, and posts everything to the ticket. Runs before /implement.
disable-model-invocation: true
argument-hint: "[issue-number] [--bugfix | --refactoring]"
---

# Design the implementation for GitHub issue $ARGUMENTS

The **design stage** that precedes `/implement`. It turns a GitHub issue into a
validated design posted to the ticket. It does **no** product coding —
`/implement` does that next. The repo is `CGaul/memry`,
a single Python package (`src/memry`, Python ≥3.11, built with hatchling) that serves a CLI, a REST API with an inline dashboard, and MCP over Starlette/uvicorn, with SQLite as its only production store; CLAUDE.md (already in your context) describes the
architecture.

## Outputs and completion

- Writes to `.gh-work/<n>/`: `issue.json`, `issue_body.md`, `metadata.json`,
  `architectural_analysis.md`, `implementation_plan.md`,
  `design_validation.json`, `affected_classes.mmd`, then
  `affected_classes_section.md` and `design_comment.md`.
- AWM completes this step as soon as the analysis, the plan **and** either
  `affected_classes_section.md` or `design_comment.md` exist. So write those
  last two only after the issue-body update and the design comment have been
  posted (Step 6) — written earlier, they end the step before the ticket
  carries the design.
- `design_validation.json` is rewritten on every design-review round: its
  fresh mtime is how AWM sees that the next round is ready.
- **Running unattended.** Under AWM (`AWM_WORKFLOW_ID` set) nobody watches
  this terminal: a turn that ends without a tool call stops the work, and AWM
  reads it as a request for input. Keep going until `design_comment.md` is
  written. Put progress notes in the same message as your next tool call. Do
  not end a turn with a summary that announces the next step, an offer to
  continue, or a list of decisions none of which blocks the work. End a turn
  only when `design_comment.md` exists, when a decision needs the human (ask
  with AskUserQuestion), or when something blocks you — then say plainly what
  blocks you.
- Boundary: no product code, no commits. If a step fails, stop and surface the
  error rather than working around it.

## Modes

- `--bugfix` — root-cause analysis becomes part of the architectural analysis.
- `--refactoring` — behaviour is frozen. Read the ticket's
  `## Behavior Preservation Contract`, `## Scope` and `## Out of Scope`, and
  `.gh-work/<n>/agent_context.md` when present (the interview's full depth).
  The design may only reshape code inside Scope, must not touch anything in
  Out of Scope, and the plan names every contract capture `/implement` will
  run before and after the change.
- No flag — a feature.

If no issue number is given, ask for one.

## Preamble — resolve paths once

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
WORK_DIR="$REPO_ROOT/.gh-work/<n>"    # shared with /implement
mkdir -p "$WORK_DIR"
```

## Procedure

### Step 1 — Fetch the issue + metadata (idempotent)

Reuse `$WORK_DIR/issue.json` if present, else fetch it:

```bash
command gh api repos/CGaul/memry/issues/<n> > "$WORK_DIR/issue.json"
```

On a 404, report and stop. Extract the body to `$WORK_DIR/issue_body.md`, then
parse the ticket's `## Implementation metadata` block into
`$WORK_DIR/metadata.json` with exactly these keys (the AWM orchestrator reads
this file to learn the branch):

```json
{ "target_branch": "…", "branch_originates_from": "…", "target_branch_on_pr": "…" }
```

(Values come from the `* Target Branch:` / `* Branch originates from:` /
`* Target branch on PR:` lines; use `main` for any missing value.)

If the issue body contains a `## API Contract` section and `$WORK_DIR` holds
no `*.openapi.yaml` file, restore each fenced spec from that section to
`$WORK_DIR/<name>.openapi.yaml` (the bold line above each fence names the
file). This keeps the contract visible in AWM's Artifacts tab and available
to `/implement` on a fresh checkout.

### Step 2 — Analyze the codebase

Read every module the ticket touches (follow imports across
`src/memry/` (its top-level modules plus `backends/`, `intelligence/`, `providers/` and `evals/`) — a change in a shared/inner module ripples outward).
Use graphify to scope it before reading broadly (`graphify explain` first,
check its `Source:` line, then pass the printed ID to `graphify affected`).
For a bugfix: reproduce the failure path in the code and establish the root
cause before designing anything. For a refactoring: confirm each smell in the
ticket's Smell Inventory still holds in the current code.

Write `$WORK_DIR/architectural_analysis.md`: current state of the affected
modules, the constraints that actually bind this change (layer direction,
persisted-data compatibility, public contracts other code or other repos rely
on — name each one and say whether the design keeps it), root cause (for
bugfixes), and the impact surface per module or layer.

### Step 3 — Write the implementation plan

Write `$WORK_DIR/implementation_plan.md` under a `## Implementation Plan`
heading: numbered steps, each naming the files to change, the shape of the
change, and its verification. The gates are the two CI jobs in `.github/workflows/publish.yml`: `python -m pytest -q` (the suite in `tests/`, about 40 s, which also parses the inline dashboard JS with `node --check`) and `python deploy/release_check.py` (release drift: any change under `src/memry`, `pyproject.toml`, `Dockerfile` or `requirements-docker.txt` needs a new `__version__` in `src/memry/__init__.py`, so such a change bumps the patch version, as every merged PR here does). There is no typecheck or lint gate. Name any runtime check
explicitly — the command to run and what output proves the step.
When a running instance is needed, plan it as an isolated one
(`./run.sh --port-offset 100`, which shifts every host port), never as a
restart of the developer's own running instance.

When `$WORK_DIR` holds `*.openapi.yaml` contracts, plan steps that touch
endpoints must conform to them; if the analysis shows a contract must change,
update both the artifact file and the ticket's `## API Contract` section, and
flag the change in the design comment.
Order steps so the tree stays green after every step.
Note required DB migrations (additive `ALTER TABLE … ADD COLUMN` steps that run when the database opens, each guarded by a `PRAGMA table_info` check: `LocalBackend` in `backends/local.py`, and `accounts.py` for the login database; there is no migrations directory and no schema version) explicitly.

For `--refactoring`, add a `### Behavior Preservation` subsection listing each
contract capture verbatim (the exact command from the ticket) — `/implement`
runs exactly these before and after its change.

### Step 4 — Validate the design (one pass)

Check the design once and write `$WORK_DIR/design_validation.json`:

```json
{ "verdict": "PASS" | "FINDINGS",
  "coverage": [ { "criterion": "<acceptance criterion>", "plan_step": "<n>" } ],
  "layers": [ { "layer": "<module/layer touched>", "rule": "<layering rule>", "respected": true } ],
  "findings": [ { "issue": "…", "resolution": "…" } ] }
```

- `coverage`: every Acceptance Criterion of the ticket mapped to the plan step
  that delivers it.
- `layers`: every touched layer with the rule it must respect
  (imports point inward only: surfaces (`cli.py`, `rest.py`, `mcp_server.py`) → `store.py` → `intelligence/`, `retrieval.py`, `filters.py` → `providers/`, `backends/`, with `models.py` and `config.py` shared; surfaces call `MemoryStore` instead of implementing memory behaviour) and whether the plan respects it.
- For `--refactoring`, every contract capture named in the plan; when
  present, every planned endpoint matched against its
  `$WORK_DIR/*.openapi.yaml` spec.

A criterion without a plan step, or a broken layer rule, is a finding: revise
the analysis/plan once, then rewrite the file. Findings that remain are carried
into the design comment (Step 6).

### Step 5 — Affected-modules diagram

Write `$WORK_DIR/affected_classes.mmd`: a mermaid `classDiagram` (or
`flowchart` when more honest) of the changed/new modules, grouped per layer or
package, with changed nodes highlighted and their direct dependencies
included.

### Step 6 — Post to the issue, then write the completion artifacts

Append three sections to the issue body — `## Architectural Analysis`,
`## Implementation Plan`, `## Affected Classes Diagram` (the `.mmd` wrapped in
a ```` ```mermaid ```` fence) — each only if that heading is not already
present (fetch the current body, append, then `gh issue edit <n> --body-file …`).
Keep the total body under GitHub's 65,536 char limit; if it would overflow,
summarize the analysis prose (never the plan's file lists) and note the
truncation. Match the length of every section to what the implementer and
reviewer need: cover the substance, no filler sections, no restated summaries.

Compose the design summary plus any residual validation findings in a temp
file outside `$WORK_DIR` and post it:

```bash
command gh issue comment <n> --repo CGaul/memry --body-file <temp-comment>
```

Only after both GitHub writes succeeded, write the two completion artifacts —
the diagram section first, the comment last:

```bash
{ printf '```mermaid\n'; cat "$WORK_DIR/affected_classes.mmd"; printf '```\n'; } > "$WORK_DIR/affected_classes_section.md"
cp <temp-comment> "$WORK_DIR/design_comment.md"
```

### Step 7 — Hand off

Report: design posted, validation outcome (PASS / N residual findings), and
the next step — `/implement <n>` (add `--bugfix` or `--refactoring` if
applicable). Return the design summary so a caller can derive the branch
description.

## Error handling

| Scenario | Action |
|----------|--------|
| Missing issue number | Ask the user |
| Issue not found (404) | Report and stop |
| An artifact is missing/empty after its step | Stop loud — do not continue |
| Findings remain after the one revision | Proceed; fold them into the design comment and surface them |
| A design section already exists on the issue | Do not duplicate it; surface that the design was likely already posted |
| `--refactoring` ticket without a Behavior Preservation Contract | Stop — the ticket must be re-planned with `/plan-refactoring` |
