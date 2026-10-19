---
name: plan-feature
description: Interactive interview that produces a well-structured GitHub feature ticket — the recommended entry point ahead of /design-implementation.
disable-model-invocation: false
argument-hint: "[optional short feature topic]"
---

# Plan GitHub Feature $ARGUMENTS

Drive a **disciplined interview** with the user, then create a GitHub feature
ticket in a fixed structured format that `/design-implementation` and
`/implement` can parse deterministically. Run **inline** in the main
conversation — the user must see and refine each section verbatim.

The repo is `CGaul/memry`.

## Outputs and completion

- Creates one GitHub issue (label `feature` when it exists), then writes
  `.gh-work/<n>/issue.json` — the completion signal AWM binds the workflow to —
  plus one `.gh-work/<n>/<name>.openapi.yaml` per signed-off API contract.
- Fetch `issue.json` only after the issue number is stamped into the Target
  Branch line (Step 7): AWM reads the branch from it.
- The human signs off on the rendered draft before any GitHub write. This is a
  human-in-the-loop interview: ask, then wait for the answer.
- Open questions are resolved during the interview and never written to the
  ticket; Acceptance Criteria are verifiable conditions, never "make it work".

## Operating principles — apply to the whole interview

- **Don't assume. Don't hide confusion. Surface tradeoffs.** When something is
  unclear, ask. When two interpretations exist, present both. When a simpler
  approach exists, say so. Push back when warranted.
- **Resolve open questions during the interview.** Fold the answers into
  How? / Alternatives / Acceptance Criteria.
- **Capture standing assumptions explicitly** in `## Assumptions` so the
  implement stage can flag a violation instead of silently working around it.
- **Draft, then confirm.** You draft every section yourself from the topic and
  the code; the user corrects. Mark everything you inferred rather than heard
  with `_(inferred)_` so it is easy to spot, and show the drafted sections
  verbatim.
- **Discrete choices go through AskUserQuestion** (an A/B tradeoff, approve vs
  change, a default to accept) — AWM can answer those from its UI. Keep free
  text for open questions.
- If the invocation carries **"Notes for the interview"** (branch origination,
  PR target branch, attached screenshot paths), honor them: Read attached
  images before drafting, and use the branch notes as the Implementation
  metadata defaults instead of asking again.

## Procedure

### Step 1 — Opener

Read `$ARGUMENTS` as the starting topic. If it already describes the feature,
use it; otherwise ask the user to describe the feature in one short paragraph.

### Step 2 — Ground the draft

Read the code the feature would touch (graphify first, then the modules) so
the How?, the assumptions and the acceptance criteria are drafted against the
real system, not a guess.

### Step 3 — Draft every section

Draft all of these yourself:

1. **User Story** — `As a <user type> I want to <action>, so that I can
   <goal/benefit>.`
2. **Why?** — business value, not feature-talk.
3. **How?** — abstract, system-level approach only; no code-level detail
   (that's the design stage's job).
4. **API contract (conditional)** — only when the How? adds or changes HTTP
   API endpoints: the **delta** as an OpenAPI 3.1 spec (just the new/changed
   paths and their request/response schemas — never the whole API). This is
   the one deliberate exception to "no code-level detail": the contract is
   what the implement stage builds against. When several distinct API areas
   change, draft one spec per area. Skip it silently when no API surface
   changes.
5. **Assumptions** — standing assumptions the requester is making.
6. **Acceptance Criteria** — verifiable, ideally `Given X, when Y, then Z`;
   at least one checkbox bullet (`- [ ]`).
7. **Out of Scope** — what this ticket intentionally does NOT cover.
8. **Alternatives Considered** — at least one alternative + rationale.
9. **Implementation metadata values** — branch slug (short, hyphenated),
   branch originates from (default `main`), target branch on PR (default
   `main`), relates to (links or "—").

Collect the **open questions** your draft depends on — they are what the
batches below resolve.

### Step 4 — Confirm in two batches

- **Batch 1** — User Story, Why?, How? (with any API contract) and Acceptance
  Criteria, together with the open questions. Push back on vague roles,
  actions and criteria.
- **Batch 2** — Assumptions, Out of Scope, Alternatives Considered and the
  metadata values.

Show each batch verbatim, ask, and fold the answers in. Repeat a batch only
when the answers changed it materially.

### Step 5 — Render the ticket and get sign-off

The `## Implementation metadata` block is **always the first section** of the
ticket body, in exactly this shape (the issue number is stamped in Step 7):

```markdown
## Implementation metadata
* Target Branch: feature/NNN_<branch-slug>
* Branch originates from: <base>
* Target branch on PR: <pr-base>
* Tenant: all
* Relates to: <links or "—">
```

The `* Tenant:` line is **required by AWM's ticket parser** and must hold a
real literal — `—`/`n/a`/`tbd` are rejected as placeholders, which would make
AWM treat the whole ticket as unstructured and re-plan it.

Then the authored sections below it, using these exact headings:
`## User Story`, `## Why?`, `## How?`, `## Assumptions`,
`## Acceptance Criteria`, `## Out of Scope`, `## Alternatives Considered`.

When an API contract was drafted, add a `## API Contract` section directly
after `## How?`, holding each signed-off spec in a ` ```yaml ` fence (one
fence per spec, each preceded by its artifact filename as a bold line, e.g.
`**api_contract.openapi.yaml**`). The ticket is the durable copy — the
design and implement stages restore the artifact file from this section
when the local copy is missing.

Show the full rendered draft and ask for approval (AskUserQuestion: approve /
request changes). On change requests, revise and show it again. Do not create
the issue without an explicit approval.

### Step 6 — Create the issue

Derive a concise verb-phrase title. Then:

```bash
command gh issue create --repo CGaul/memry \
  --title "<title>" --body-file <draft-file> --label feature 2>/dev/null \
  || command gh issue create --repo CGaul/memry \
       --title "<title>" --body-file <draft-file>
```

(The fallback covers a missing `feature` label — never invent labels.)

### Step 7 — Stamp the number + write the AWM artifact

Extract the issue number `<n>` from the returned URL, replace `NNN` in the
Target Branch line with `<n>`, and update the issue:

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

### Step 8 — Report

Print the issue URL and the next steps: `/design-implementation <n>`, then
`/implement <n>` (add `--worktree` for a separate git worktree). For a bug,
the sibling entry point is `/plan-bugfix`. If an API contract was written,
name the `.openapi.yaml` artifact(s) — they are the source of truth the
implement stage builds the API against.
