---
name: implement
description: End-to-end implementation of a designed GitHub feature, bugfix or refactoring issue — runs after /design-implementation: sets up the branch, implements the plan, runs the gates and one plan-validation pass, and opens a merge-ready PR.
disable-model-invocation: true
argument-hint: "[issue-number] [--bugfix | --refactoring] [--tenant <name>] [--worktree]"
---

# Implement GitHub issue $ARGUMENTS

The **implementation stage** of the pipeline
`/plan-feature|/plan-bugfix|/plan-refactoring` → `/design-implementation` →
**`/implement`**. The repo is `CGaul/memry`, a single Python package (`src/memry`, Python ≥3.11, built with hatchling) that serves a CLI, a REST API with an inline dashboard, and MCP over Starlette/uvicorn, with SQLite as its only production store.
The gates are the two CI jobs in `.github/workflows/publish.yml`: `python -m pytest -q` (the suite in `tests/`, about 40 s, which also parses the inline dashboard JS with `node --check`) and `python deploy/release_check.py` (release drift: any change under `src/memry`, `pyproject.toml`, `Dockerfile` or `requirements-docker.txt` needs a new `__version__` in `src/memry/__init__.py`, so such a change bumps the patch version, as every merged PR here does). There is no typecheck or lint gate.
## Outputs and completion

- Writes to `.gh-work/<n>/`: `metadata.json` (Step 1, before any code),
  `implementation_summary.md`, `plan_validation.md`, and last `pr_body.md`.
- AWM completes this step when `plan_validation.md` carries a
  `VERDICT: PASS` (or `VERDICT: PASS_WITH_RESIDUALS`) line **and**
  `pr_body.md` exists. Write `pr_body.md` only after `gh pr create` succeeded
  — the next step audits that the PR exists.
- **Running unattended.** Under AWM (`AWM_WORKFLOW_ID` set) nobody watches
  this terminal: a turn that ends without a tool call stops the work, and AWM
  reads it as a request for input. Keep going until `pr_body.md` is written.
  Put progress notes in the same message as your next tool call. Do not end a
  turn with a summary that announces the next step, an offer to continue, or a
  list of decisions none of which blocks the work. End a turn only when
  `pr_body.md` exists, when a decision listed here needs the human (ask with
  AskUserQuestion), or when something blocks you — then say plainly what
  blocks you. This does not lift any confirmation required before risky or
  outward actions.
- Decisions that need the human: falling back to `origin/main` when the base
  branch is missing (Step 3); opening the PR with residual findings (Step 5).
- Scope: deliver what the plan asks, at its scope. Don't add features,
  refactors, docs or files the plan and this skill don't require; if one would
  help, list it in the summary instead. Don't use subagents to verify your own
  work — the gates and Step 5 are the check.
- Runtime checks run against an isolated instance —
  `./run.sh --port-offset <N>` shifts every host port it binds — never against
  the developer's own running instance or its data, which a shared port or
  database would disturb.

## Flags

- `--bugfix` — branch/PR prefix `bugfix/` instead of `feature/`.
- `--refactoring` — branch/PR prefix `refactor/`; behaviour is frozen and the
  ticket's Behavior Preservation Contract is captured before and after the
  change. Read `refactor.md` in this skill's directory before Step 4 — it is
  the procedure for this mode.
- `--tenant <name>` — accepted for AWM's uniform invocation contract and
  ignored: this project has no tenant domain.
- `--worktree` — set up the branch in a separate git worktree; all subsequent
  steps run inside it.

If no issue number is given, ask for one.

## Preamble — resolve paths once, before any `--worktree` cd

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
WORK_DIR="$REPO_ROOT/.gh-work/<n>"    # absolute; shared with /design-implementation
mkdir -p "$WORK_DIR"
```

Keep `<kind>` (feature|bugfix|refactoring) in mind throughout. If a step
fails, **stop and surface the error**.

## Procedure

### Step 1 — Fetch the issue and extract metadata

Reuse `$WORK_DIR/issue.json` if present, else
`command gh api repos/CGaul/memry/issues/<n> > "$WORK_DIR/issue.json"`
(404 → report and stop). Extract the body to `$WORK_DIR/issue_body.md` and
parse the `## Implementation metadata` block into `$WORK_DIR/metadata.json`:

```json
{ "target_branch": "…", "branch_originates_from": "…", "target_branch_on_pr": "…" }
```

Write this file **now, before coding starts** — the AWM orchestrator reads
`target_branch` from it to track the branch and later find the PR.

If the issue body contains a `## API Contract` section and `$WORK_DIR` holds
no `*.openapi.yaml` file, restore each fenced spec from that section to
`$WORK_DIR/<name>.openapi.yaml` (the bold line above each fence names the
file). These contracts are the source of truth for any API work in Step 4,
and AWM's Artifacts tab renders them via Swagger UI.

### Step 2 — Require the design

Reuse `$WORK_DIR/architectural_analysis.md` and
`$WORK_DIR/implementation_plan.md`. If either is missing or empty, stop and
report that the design step (`/design-implementation <n>`) must run first —
that skill is user-invoked only, so it cannot be run from here.

### Step 3 — Set up the branch

The branch name is `target_branch` from metadata.json (shape
`<kind>/<n>_<slug>`). Base it on `branch_originates_from`.

**The branch may already exist**: an orchestrator (AWM) cuts it from the base
branch *before* the planning stage, so the interview and design run on current
code and skills instead of whatever the checkout was left on. Reuse it —
creating it is only your job when nobody did it for you:

```bash
git fetch origin
if git rev-parse --verify --quiet "refs/heads/<target_branch>"; then
  git checkout <target_branch>          # prepared before this run — reuse it
else
  git checkout -b <target_branch> origin/<branch_originates_from>
fi
```

Always fetch and branch from `origin/<base>`, never from a local base branch:
a stale local base produces a PR against a superseded tree.

With `--worktree`, a branch already checked out elsewhere cannot be claimed by
a new worktree — park that checkout back on the base branch first:

```bash
git worktree add "$WORKTREE" <target_branch>          # branch exists
git worktree add "$WORKTREE" -b <target_branch> origin/<branch_originates_from>   # it does not
cd "$WORKTREE"
```

Verify you ended up on `<target_branch>`; if not, stop. If the base branch does
not exist on the remote, stop and ask before falling back to `origin/main` (and
note the fallback in the PR body).

### Step 4 — Implement the plan

For `--refactoring`, capture the baseline first (`refactor.md`).

Work through `implementation_plan.md` step by step, in order. After each step
the tree must stay green
before you move on; commit in coherent units with messages referencing the
issue (`… (#<n>)`). Match the surrounding code's idiom (see CLAUDE.md). When
an Acceptance Criterion has a runtime surface, verify it and record what you
observed.

Standing contracts while implementing:

- **Layer direction** — never add an import that points outward
  (imports point inward only: surfaces (`cli.py`, `rest.py`, `mcp_server.py`) → `store.py` → `intelligence/`, `retrieval.py`, `filters.py` → `providers/`, `backends/`, with `models.py` and `config.py` shared; surfaces call `MemoryStore` instead of implementing memory behaviour), even where nearby code already does: each such
  import makes the next layering refactor larger.
- **Migrations are additive** (additive `ALTER TABLE … ADD COLUMN` steps that run when the database opens, each guarded by a `PRAGMA table_info` check: `LocalBackend` in `backends/local.py`, and `accounts.py` for the login database; there is no migrations directory and no schema version) — an existing database
  must keep working after the upgrade, so add columns and tables; never
  repurpose or drop them in the same change.
- **API contract** — when `$WORK_DIR` holds `*.openapi.yaml` specs, endpoint
  work must match them exactly (paths, methods, schemas). If implementation
  forces a deviation, update the spec file **and** the ticket's
  `## API Contract` section to the as-built shape, and record the deviation
  with rationale in `implementation_summary.md` — the artifact in AWM must
  never show a contract the code doesn't honor.
Then write `$WORK_DIR/implementation_summary.md`: what was built per
plan step, deviations (with rationale), verification evidence. Keep it to
what a reviewer needs — no filler sections, no restated summaries.

### Step 5 — Gates + plan validation (one pass)

1. Run the gates: `python -m pytest -q` and `python deploy/release_check.py`.
   For `--refactoring`, also the after-captures and their diff
   (`refactor.md`).
2. Map each Acceptance Criterion and each plan step to its evidence — a test
   name, a `file:line`, or a command and its output. When `$WORK_DIR`
   holds `*.openapi.yaml` contracts, each implemented endpoint maps to its
   spec (or the recorded deviation).
3. Write `$WORK_DIR/plan_validation.md`: the gate results, the evidence table,
   and a final line `VERDICT: PASS` when every gate is green and every
   criterion has evidence — otherwise `VERDICT: FINDINGS` plus the list.

On findings, do one remediation round: fix them, append a `## Remediation`
section to `implementation_summary.md`, re-run the failing gates, and rewrite
`plan_validation.md`. If findings remain after that round, ask the human
whether to open the PR anyway or halt. On "open anyway", end the file with
`VERDICT: PASS_WITH_RESIDUALS` and list each residual as a `- [ ]` checkbox in
the PR body's `## Plan validation` section.

The AWM orchestrator greps this file for the PASS verdict — never write a PASS
line while findings remain unacknowledged.

### Step 6 — Open the pull request, then write the completion artifact

Compose the PR body in a temp file (NOT in `$WORK_DIR` yet): Summary,
Architectural Approach, Changes, Verification, a `## Plan validation` section
(final verdict, plus residual checkboxes if any), for `--refactoring` the
`## Behavior preservation` section from `refactor.md`, the line
`Closes #<n>`, and the
`🤖 Generated with [Claude Code](https://claude.com/claude-code)` footer.

```bash
command gh issue comment <n> --repo CGaul/memry --body-file "$WORK_DIR/implementation_summary.md"
git push -u origin HEAD
command gh pr create --repo CGaul/memry \
  --base <target_branch_on_pr> \
  --title "<kind>/<n> - <short description>" \
  --body-file <temp-pr-body>
command gh pr view <pr> --repo CGaul/memry --json mergeable   # UNKNOWN → re-query after a few seconds
cp <temp-pr-body> "$WORK_DIR/pr_body.md"
```

The PR base must be `target_branch_on_pr` from Step 1. Before writing
`pr_body.md`, confirm the PR is mergeable: `CONFLICTING` means the branch was
cut from a stale base — resolve it now rather than hand over a PR that cannot
merge.

### Step 7 — Report

Report the PR URL — and the worktree path, if one was created — to the user.

## Error handling

| Scenario | Action |
|----------|--------|
| Missing issue number | Ask the user |
| Issue not found (404) | Report and stop |
| Design artifacts missing | Stop — the design step must run first |
| Base branch not on remote | Stop and ask before falling back to origin/main |
| Findings remain after the remediation round | Ask whether to open the PR with residuals (`PASS_WITH_RESIDUALS`) or halt |
| A gate fails | Fix before proceeding — never push a red tree, never weaken the gate |
| PR is `CONFLICTING` | Resolve the conflict before writing `pr_body.md` |
