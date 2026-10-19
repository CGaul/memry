---
name: plan-bugfix
description: Interactive interview that produces a well-structured GitHub bugfix ticket (new or from an existing issue via --gh-issue) — entry point ahead of /design-implementation.
disable-model-invocation: false
argument-hint: "[symptom text | --gh-issue <id>]"
---

# Plan GitHub Bugfix $ARGUMENTS

Drive a **disciplined interview** about a bug, then create (or update) a
GitHub ticket in a fixed structured format that `/design-implementation` and
`/implement` can parse deterministically. Run **inline** — the user must see
and refine each section verbatim.

The repo is `CGaul/memry`.

## Outputs and completion

- Creates a GitHub issue (label `bug` when it exists) or, with `--gh-issue`,
  restructures that issue in place — never a duplicate. Then writes
  `.gh-work/<n>/issue.json` — the completion signal AWM binds the workflow to —
  plus one `.gh-work/<n>/<name>.openapi.yaml` per signed-off API contract.
- Fetch `issue.json` only after the issue number is stamped into the Target
  Branch line (Step 5): AWM reads the branch from it.
- The human signs off on the rendered draft before any GitHub write. This is a
  human-in-the-loop interview: ask, then wait for the answer.
- `## Regression Prevention` is mandatory, and a root-cause guess is never
  presented as fact.

## Operating principles

Same discipline as `/plan-feature`: don't assume, surface confusion and
tradeoffs, resolve open questions during the interview (never park them in the
ticket), capture standing assumptions, refuse "make it work". Draft every
section yourself, mark what you inferred with `_(inferred)_`, and confirm in
batches; put discrete choices (priority, an A/B fix approach, approve vs
change) through AskUserQuestion, which AWM can answer from its UI.

Additionally for bugs: **a regression-prevention strategy is mandatory** — how
do we ensure this exact failure cannot silently return. Name the concrete
mechanism: ideally an automated test in the project's suite
(`python -m pytest -q`), else a type- or lint-visible constraint or a
validation step. If the invocation carries **"Notes for the interview"**
(branch origination, PR target, screenshot paths), honor them: Read the images
first and use the branch notes as metadata defaults.

## Step 0 — Resolve the input mode

1. **Free text** — `/plan-bugfix <symptom>` (or no args): if the symptom text
   is too thin to draft from, ask for Observed vs Expected first; creates a
   **new** issue in Step 4.
2. **Existing issue** — `--gh-issue <id>`: fetch it
   (`gh issue view <id> --comments --repo CGaul/memry`),
   infer the bugfix sections from the body/comments as the draft, and **update
   the same issue** in Step 4 (no duplicate).

## Step 1 — Ground and draft every section

Read the code on the failure path, then draft:

1. **Bug Description** — *Observed* vs *Expected* behavior, precisely.
2. **Reproduction** — ordered, concrete steps (which page/route, which
   endpoint or command, which data or state); environment details that
   matter.
3. **Root Cause** — if known or hypothesized; "unknown — to be analyzed in the
   design stage" is acceptable, a guess presented as fact is not.
4. **How?** — abstract fix approach; no code-level detail.
5. **API contract (conditional)** — only when the fix changes or extends an
   HTTP API surface (new endpoint, changed path/method, request/response
   schema, status codes): the **delta** as an OpenAPI 3.1 spec exactly as
   `/plan-feature` does. Skip it silently when the fix has no API surface.
6. **Assumptions** — standing assumptions.
7. **Acceptance Criteria** — verifiable; at least one checkbox bullet
   (`- [ ]`); must include "the observed behavior no longer reproduces via the
   reproduction steps".
8. **Regression Prevention** — the concrete mechanism (mandatory).
9. **Out of Scope** / **Alternatives Considered**.
10. **Priority** — `critical` / `high` / `medium` / `low`.
11. **Implementation metadata values** — branch slug, branch originates from
    (default `main`), target branch on PR (default `main`), relates to.

## Step 2 — Confirm in two batches

- **Batch 1** — Bug Description, Reproduction, Root Cause, How? (with any API
  contract), Acceptance Criteria and Regression Prevention, together with the
  open questions.
- **Batch 2** — Assumptions, Out of Scope, Alternatives Considered, Priority
  (AskUserQuestion) and the metadata values.

Show each batch verbatim, ask, and fold the answers in.

## Step 3 — Render the ticket and get sign-off

`## Implementation metadata` is always the first section (issue number is
stamped in Step 5):

```markdown
## Implementation metadata
* Target Branch: bugfix/NNN_<branch-slug>
* Branch originates from: <base>
* Target branch on PR: <pr-base>
* Tenant: all
* Priority: <priority>
* Relates to: <links or "—">
```

The `* Tenant:` line is **required by AWM's ticket parser** and must hold a
real literal — `—`/`n/a`/`tbd` are rejected as placeholders, which would make
AWM treat the whole ticket as unstructured and re-plan it.

Then, with these exact headings: `## Bug Description`, `## Reproduction`,
`## Root Cause`, `## How?`, `## Assumptions`, `## Acceptance Criteria`,
`## Regression Prevention`, `## Out of Scope`, `## Alternatives Considered`.

When an API contract was drafted, add a `## API Contract` section directly
after `## How?`, holding each signed-off spec in a ` ```yaml ` fence, each
preceded by its artifact filename as a bold line (e.g.
`**api_contract.openapi.yaml**`). The ticket is the durable copy — the
design and implement stages restore the artifact file from this section
when the local copy is missing.

Show the full rendered draft and ask for approval (AskUserQuestion: approve /
request changes). On change requests, revise and show it again. Do not touch
GitHub without an explicit approval.

## Step 4 — Create or update the issue

- **New issue**: derive a concise title describing the *fix*, then

  ```bash
  command gh issue create --repo CGaul/memry \
    --title "<title>" --body-file <draft-file> --label bug 2>/dev/null \
    || command gh issue create --repo CGaul/memry \
         --title "<title>" --body-file <draft-file>
  ```

- **Existing issue** (`--gh-issue`): `gh issue edit <id> --body-file …` with
  the restructured body (preserve the original report by appending it under a
  `## Original report` section if it would otherwise be lost).

## Step 5 — Stamp the number + write the AWM artifact

Extract the issue number `<n>`, replace `NNN` in the Target Branch line, and:

```bash
command gh issue edit <n> --repo CGaul/memry --body-file <stamped-draft-file>
REPO_ROOT="$(git rev-parse --show-toplevel)"
mkdir -p "$REPO_ROOT/.gh-work/<n>"
command gh api repos/CGaul/memry/issues/<n> > "$REPO_ROOT/.gh-work/<n>/issue.json"
```

When an API contract was drafted, also write each signed-off spec to
`"$REPO_ROOT/.gh-work/<n>/<name>.openapi.yaml"` (default name
`api_contract`; use a short area name per spec when there are several).
The filename **must end in `.openapi.yaml`** — AWM's Artifacts tab detects
that suffix and renders the file with Swagger UI. These specs are optional
artifacts, not completion signals.

## Step 6 — Report

Print the issue URL and the next steps: `/design-implementation <n> --bugfix`,
then `/implement <n> --bugfix`. If an API contract was written, name the
`.openapi.yaml` artifact(s) — they are the source of truth the implement
stage builds the API against.
