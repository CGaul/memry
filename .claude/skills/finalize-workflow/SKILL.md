---
name: finalize-workflow
description: Close out an AWM workflow — brief the human on what it achieved and what they steered, then on their decision merge the PR and close the ticket, or discard it. Runs last, after /code-review.
disable-model-invocation: true
argument-hint: "<github-issue-number>"
---

# Finalize workflow for GitHub issue $ARGUMENTS

The **final stage** of the AWM pipeline
`… → ci → /code-review` → **`/finalize-workflow`**.
The repo is `CGaul/memry`.

## Outputs and completion

- Mode A writes `.gh-work/<n>/finalize_summary.md` and nothing else; AWM then
  turns the step into a human gate. Mode B performs the decision and writes
  `.gh-work/<n>/finalize.json` **last, exactly once** — it completes the step,
  so writing it in Mode A or early in Mode B ends the workflow wrongly.
- **Running unattended (Mode B).** Under AWM (`AWM_WORKFLOW_ID` set) nobody
  watches this terminal: a turn that ends without a tool call stops the work,
  and AWM reads it as a request for input. Keep going until `finalize.json` is
  written. Put progress notes in the same message as your next tool call. Do
  not end a turn with a summary that announces the next step, an offer to
  continue, or a list of decisions none of which blocks the work. End a turn
  only when `finalize.json` exists — a failure is recorded in it as
  `"status": "failed"`, not reported instead of it. The human's Accept/Discard
  is the confirmation for the merge or close; this does not lift any other
  confirmation.

This skill runs **twice in the same session**, and which run you are in is
decided by one file:

- **Mode A — brief.** `.gh-work/<n>/finalize_input.json` has `"decision": null`
  (or no `decision` key). Write the brief and **stop**. Touch nothing else.
- **Mode B — decision.** The same file carries
  `{"decision": {"decision": "accept"|"discard", "note": "…"}}`, and the prompt
  that resumed you names it. Perform the side effects, then write
  `finalize.json`.

The prompt AWM resumes you with restates the whole command sequence, so a
session whose context has drifted still gets it right. When the prompt and this
file disagree, **the prompt wins** — it was written against the live workflow.

## Preamble

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
WORK_DIR="$REPO_ROOT/.gh-work/<n>"
```

`finalize_input.json` (written by the AWM daemon before this session launched)
is the manifest. Read it first. If it is missing or unreadable, fall back to
`gh issue view <n>` plus `gh pr list --head <branch>` and say so in the brief —
never stop for it.

Its shape:

```json
{
  "schemaVersion": 1,
  "workflowId": 30, "kind": "feature", "topic": "…",
  "ticketId": 11, "ticketTitle": "…", "ticketUrl": "…",
  "branch": "feature/11_…", "prNumber": 42, "prUrl": "…",
  "notion": { "pageId": "…", "pageUrl": "…", "databaseId": "…" },
  "sessions": [{ "step": "design", "label": "Design", "ordinal": 3,
                 "claudeSessionId": "…", "transcriptPath": "…",
                 "role": "step_runner", "startedAt": "…" }],
  "lineage": [{ "step": "…", "summary": "…", "actor": "…", "createdAt": "…" }],
  "reviews": [{ "step": "developer_review", "round": 1, "decision": "changes",
                "comments": [{ "subject": "shot.png", "comment": "…" }],
                "createdAt": "…" }],
  "decision": null
}
```

`notion` is `null` when the workflow never bound a Notion page, and
`sessions[].transcriptPath` may be `null` for a session whose file is gone.

---

## Mode A — write the brief

### A1. Extract each session's human turns

For **every** entry in `sessions` that has a `transcriptPath`:

```bash
python3 .claude/skills/finalize-workflow/scripts/extract_decisions.py \
  "<transcriptPath>" --step "<step>" --max-chars 4000
```

The script prints
`{"step","transcript","entries":[{"timestamp","text"}],"truncated"}` and
**always exits 0** — a missing file or an unrecognised transcript shape yields
`{"entries": []}`. That is deliberate: the transcript format is a Claude Code
internal, so a format change must cost you one session's decisions, never the
brief. Run it once per session, in the order `sessions` gives (pipeline order).

Do **not** read a transcript directly. The largest one measured on this repo is
33 MB; the script filters it to ~25 KB of human-authored text, which is the
only reason this step fits in a context window at all.

### A2. Write `.gh-work/<n>/finalize_summary.md`

Three sections, in this order:

1. **What this workflow set out to do** — the topic and the ticket's User Story
   (or Bug Description / Refactoring Goal), in two or three sentences.
2. **What it achieved** — from `lineage`, the PR (`gh pr view <pr> --json
   title,additions,deletions,changedFiles,state`) and the ticket. State the
   outcome, not the step list.
3. **Decisions & pushbacks** — one subsection per step, in pipeline order,
   headed with the step's label. Under each, quote the human turns that
   *decided* something: an interview answer, a "no, do it this way", a review
   comment, a correction. Attribute every quote to the step it was given in.
   Fold in the matching `reviews` rounds — those come straight from AWM's
   database and are the record even when a transcript is unreadable.

Judgment, not transcription: skip "continue", "go on" and slash commands unless
they carried an instruction. If a step produced nothing worth quoting, say so
in one line rather than padding it. Aim for something a person will actually
read before clicking a button — roughly 400–800 words.

### A3. Stop

Do **not** touch GitHub or Notion in Mode A. The brief is an artifact and a
panel, nothing more. Do **not** write `finalize.json` — that file is what tells
AWM the step is finished, and writing it now ends the workflow with no decision
made.

---

## Mode B — perform the decision

Run the commands **in order** and **stop at the first failure**. On a failure,
skip the rest, and write `finalize.json` with `"status": "failed"` and the
error in `errors`. A half-run is a legitimate outcome that AWM shows the human
and lets them retry — a *wrongly reported* one is not.

### Accept

```bash
command gh pr merge <pr> --merge --repo CGaul/memry
command git push origin --delete <branch>
command gh issue close <n> --repo CGaul/memry
# then: Notion (below)
```

- `--merge` — a merge commit, deliberately. The implement step's commit
  sequence is part of the record this feature exists to preserve, so never
  `--squash` and never `--rebase`.
- **Not** `gh pr merge --delete-branch`: its local half cannot succeed while the
  branch is checked out in the AWM worktree you are running in, so it would
  half-fail on every single run. Delete the remote ref explicitly instead.
- A remote-delete that fails with **"remote ref does not exist"** (or
  equivalent) is a **success**: a repo with "automatically delete head branches"
  enabled already removed it during the merge. Never abort a merge that landed
  over that.
- Do **not** delete the local branch or the worktree. AWM tears those down
  itself, after archiving the artifacts — a session cannot remove the directory
  it is running in.

### Discard

Requires the reason (`decision.note`; the prompt carries it too). It is never
empty — AWM refuses an empty one before resuming you.

```bash
command gh issue comment <n> --repo CGaul/memry --body "<reason>"
command gh pr close <pr> --repo CGaul/memry
command gh issue close <n> --repo CGaul/memry --reason "not planned"
# then: Notion (below)
```

Close the PR **unmerged**. Destroy nothing locally: the branch, the worktree and
the artifacts all survive, because a discard is the outcome most likely to be
reconsidered.

### Notion (both paths)

Only when `notion` is non-null. Using the Notion MCP tools:

1. Fetch the page's database and its Status (or equivalent select) property
   options.
2. Pick, case-insensitively:
   - accept → a terminal state: `Done`, `Complete`, `Completed`, `Shipped`, `Live`;
   - discard → a cancelled state: `Cancelled`, `Canceled`, `Dropped`, `Won't do`,
     `Archived`.
3. Update the page to it.
4. **Accept only** — also set the closing date. The prompt names both the date
   property (AWM's per-project setting, `Fälligkeitsdatum` unless the project
   overrode it) and the date to write; use them verbatim rather than today's
   date as you compute it, and report them back in `notion.date_property` and
   `notion.date`. A database with **no property of that name** is not a failure
   and not a licence to guess a different one: move the page anyway, leave
   `notion.date` null, and put the mismatch in `notion.reason` — the human
   corrects the name in AWM's project settings. A discard sets no date.

**Never fail the step for Notion.** No bound page, no matching option, no such
date property, or an MCP error → report
`{"status": "skipped", "reason": "<why>"}` (or a `moved` with the reason, when
only the date part failed). The step still succeeds.

### Finally — write `.gh-work/<n>/finalize.json`

**Last, exactly once**, after every other action has been attempted. AWM watches
for this file to complete the step, so writing it early ends the workflow
mid-run.

```json
{
  "decision": "accept",
  "status": "completed",
  "note": "…",
  "pr": { "number": 42, "action": "merged", "url": "…" },
  "branch": { "name": "feature/11_…", "remote_deleted": true },
  "issue": { "number": 11, "closed": true, "reason": "completed" },
  "notion": {
    "status": "moved",
    "page_url": "…",
    "to": "Done",
    "reason": null,
    "date_property": "Fälligkeitsdatum",
    "date": "2026-07-27"
  },
  "errors": [],
  "finished_at": "2026-07-27T21:04:00Z"
}
```

- `decision`: `accept` | `discard` — required, and AWM ignores the file without it.
- `status`: `completed` | `failed` — required. Write `completed` **only** when
  every non-Notion action you attempted succeeded. Notion being `skipped` is
  still `completed`.
- `pr.action`: `merged` | `closed` | `skipped`.
- `issue.reason`: `completed` | `not planned`.
- `notion.status`: `moved` | `skipped` | `failed`.
- `notion.date_property` / `notion.date`: the property stamped and the date
  written into it (accept only) — both null when no date was set.
- `errors`: one plain-language string per failure; AWM shows these to the human
  verbatim and puts the step in `needs_attention`.

Then report to the user, in two or three lines, which side effects ran.

## Error handling

| Scenario | Action |
|----------|--------|
| `finalize_input.json` missing | Fall back to `gh`; note it in the brief; never stop |
| A transcript is missing/unreadable | The script returns `entries: []` — continue |
| Mode A: no PR yet | Write the brief anyway; readiness is AWM's job, not yours |
| Mode B: `gh pr merge` fails | Stop; `status:"failed"` + the error; the issue and Notion stay untouched |
| Mode B: remote branch already gone | Treat as success |
| Mode B: Notion unreachable or no matching state | `notion.status:"skipped"` with the reason; step still succeeds |
| Mode B: the named date property is absent from the database | Move the page anyway; `notion.date: null` + the mismatch in `notion.reason` |
| No issue number given | Read it from `finalize_input.json`; ask only if that fails too |
