---
name: code-review-workflow
description: AWM's code-review step — run Claude Code's builtin /code-review on this branch, then commit and push every fix it applied through the repo's own gates, and write the step's summary last. Runs after CI, before /finalize-workflow.
disable-model-invocation: true
argument-hint: "<github-issue-number> [level] [--fix] [--comment]"
---

# Code-review workflow for GitHub issue $0

The review stage of the AWM pipeline `… → ci →` **`/code-review-workflow`** `→ /finalize-workflow`.
The repo is `CGaul/memry`. Arguments: the ticket number, then
the review arguments to pass through unchanged (AWM sends `high --fix --comment`).

## Outputs and completion

- The review's `--fix` edits are **committed and pushed** to the PR branch. The
  builtin `/code-review` applies them to the working tree and stops there; the PR
  is what CI checks and finalize merges, and the worktree holding uncommitted
  fixes is removed after the merge — fixes left there are lost.
- `.gh-work/<n>/code_review.md` is written **last**, after the push. AWM completes
  the step on it, so writing it earlier lets the workflow move on without the fixes.
- **Running unattended.** Under AWM (`AWM_WORKFLOW_ID` set) nobody watches this
  terminal: a turn that ends without a tool call stops the work, and AWM reads it
  as a request for input. Keep going until `code_review.md` is written. Put
  progress notes in the same message as your next tool call. End a turn only when
  `code_review.md` exists or something blocks you — then say plainly what. This
  does not lift any confirmation required before risky actions.

## Step 1 — Note the starting point

```bash
TICKET=$0
WORK_DIR=".gh-work/$TICKET"; mkdir -p "$WORK_DIR"
BRANCH=$(git branch --show-current)
git status --porcelain --untracked-files=no   # should be empty: implement committed its work
```

Tracked files already modified now are not the review's: leave them out of the
review commit and name them in `code_review.md`.

## Step 2 — Run the review

Invoke the builtin `code-review` skill (Skill tool) with the remaining arguments
exactly as given (e.g. `high --fix --comment`). It works in the background; wait
until it has **completely** finished — every review agent done, every `--fix`
edit applied, every `--comment` finding posted — before Step 3.

## Step 3 — Commit and push the fixes

```bash
git status --porcelain --untracked-files=no
```

No changes → nothing to commit; go to Step 4 and record "no fixes".

Otherwise:

1. Run the repo's gates on the result, the way CI's `publish.yml` runs them:
   `python -m pytest -q` (job `test`, about 40 s) and
   `python deploy/release_check.py` (job `check`). There is no formatter or
   lint gate.
   A gate the review's edits broke is fixed now, in the same files, before the
   commit — a red commit would fail CI on the PR and block finalize.
2. Stage exactly the files the review changed (`git add <paths>` — never
   `git add -A`, never anything under `.gh-work/`), then commit:
   `git commit -m "Code review fixes (#$TICKET)"` with a body listing the
   findings fixed, one line each.
3. `git push origin "$BRANCH"` — never `--force`, never `--no-verify`. Rejected
   because the remote moved: `git pull --rebase origin "$BRANCH"`, re-run the
   gates if anything was replayed, push again.
4. Verify: `git status --porcelain --untracked-files=no` is empty and
   `git rev-list --count "@{u}..HEAD"` is `0`.

A push that still fails is a blocker: say so in `code_review.md` and in your
reply. Never report fixes as on the PR when they are not.

## Step 4 — Summary (last)

Write `$WORK_DIR/code_review.md`: the verdict, the confirmed findings, what was
fixed (with the commit SHA, or "no fixes"), whether the push succeeded, and what
was posted to the PR. Keep it to what a reviewer needs; no restated diff.
