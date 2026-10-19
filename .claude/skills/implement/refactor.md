# `/implement --refactoring` — behaviour-preservation procedure

Read this before Step 4 when the flag is set. Everything in SKILL.md still
applies; this adds the contract captures and the scope fence.

## Rules for this mode

- **Behaviour is frozen.** A bug you discover is reported (implementation
  summary + PR body), never fixed here — it belongs in its own ticket.
- **Scope is a fence.** Change only what the ticket's `## Scope` lists; never
  touch anything named in `## Out of Scope`. Before Step 5, compare
  `git diff --name-only origin/<branch_originates_from>...HEAD` against Out of
  Scope — any hit is a finding.
- Branch and PR prefix: `refactor/`.

## Before the first code change — capture the baseline

The ticket's `## Behavior Preservation Contract` holds lines of the form
``* Surface: <name> | capture: `<command>` ``. Copy each command, byte for
byte, into its own file — the same file runs on both sides, so the two
captures cannot drift apart by retyping:

```bash
mkdir -p "$WORK_DIR/refactor_captures" "$WORK_DIR/refactor_baseline" \
         "$WORK_DIR/refactor_after" "$WORK_DIR/refactor_diff"
# per Surface line: "$WORK_DIR/refactor_captures/<surface-slug>.sh" = the command verbatim
TOP="$(git rev-parse --show-toplevel)"   # the branch checkout the captures run in
for f in "$WORK_DIR"/refactor_captures/*.sh; do
  s=$(basename "$f" .sh)
  (cd "$TOP" && bash "$f") > "$WORK_DIR/refactor_baseline/$s.txt" 2>&1
  echo "$s exit=$?"
done
```

Run it on the freshly set-up branch, before any commit of yours. A capture that
exits non-zero at baseline means the contract is broken before your change:
stop and report it — do not adapt the command.

## In Step 5 — re-capture and diff

```bash
for f in "$WORK_DIR"/refactor_captures/*.sh; do
  s=$(basename "$f" .sh)
  (cd "$TOP" && bash "$f") > "$WORK_DIR/refactor_after/$s.txt" 2>&1
  if diff -u "$WORK_DIR/refactor_baseline/$s.txt" "$WORK_DIR/refactor_after/$s.txt" \
       > "$WORK_DIR/refactor_diff/$s.diff"; then echo "$s identical"; else echo "$s DRIFT"; fi
done
```

- An empty diff is the evidence for that surface.
- Any drift is a finding unless one of the ticket's own Acceptance Criteria
  names that exact change (e.g. a removed export together with its named
  replacement) — cite the criterion next to the diff. Unexplained drift gets
  the one remediation round; if it remains, it takes the residuals path in
  SKILL.md Step 5 (ask the human; `VERDICT: PASS_WITH_RESIDUALS` with the
  drift listed).
- When the ticket has a `## Separation Line`, run its `Check:` command too; it
  must print nothing.
- The contract's `* Gate:` line names gates that must be green as well.

## In the PR body — `## Behavior preservation`

One entry per surface: the command, the baseline output and the after output
(each in a `<details>` block when longer than ~20 lines), and the diff result
(`identical` or the diff itself). If the body would exceed GitHub's 65,536
characters, quote the first and last 30 lines of each output and name the
`.gh-work/<n>/refactor_*` files that hold the rest.
