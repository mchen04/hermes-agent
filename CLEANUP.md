# Cleanup gate — answered-unblock watermark / applicable-CI follow-up

Run ID: e1423dd0bb9f44c5bfa86607bd2cf989
Status: complete

Base: `b3ff391428956ae3b213451d356a11150247b2bf`, branch `fix/forge-continuation-20260921`.

## Passes

One pass, as bounded, over the 5-file follow-up diff. Prior branch cleanup was not re-audited and no unchanged code was reworked. The new code in `kanban_pr_acceptance.py` (`_expects_head_checks`, `_matches_ref`), `kanban_recovery.py` and `kanban_db.py` was read line by line and left as written: no dead branches, no silenced types, no defensive clutter, and every guard is reachable (a lone `[` or a leading `?`/`+` reaches the `ValueError`, the `continue` after a stale watermark is load-bearing).

## Rebase

Not needed; the coordinator checked ancestry. No push, fetch, rebase, reset or clean was run.

## Removed

- Nothing in the source diff. One simplification was drafted for the `pull_request` `types` check and reverted before commit: collapsing the nested `if` needed a defaulted sentinel tuple that read worse than the two plain lines it replaced.

## Tests deleted

none. Both new tests can fail. `test_new_pr_comment_after_answered_unblock_keeps_guard` was checked against reverted source: with both fixes reverted the answered `auto_resumed` event is read as legacy permission, the guard returns `None`, and the test fails. Each of the ten `test_unrelated_workflows_do_not_require_pr_checks` cases asserts a distinct trigger shape against a real completion result.

Note, not a deletion: the two hunks are defence in depth, not independent. Reverting only the `kanban_db.py` watermark binding still leaves the continuation test green (verified by running it against that revert), because `kanban_recovery.py` alone suppresses the legacy path. No test isolates the `kanban_db.py` hunk. Adding one is outside this bounded pass.

## Docs updated

`website/docs/user-guide/features/kanban.md`, acceptance paragraph: "an active workflow that can trigger on it holds the card as pending" described the old, broad rule. The diff narrowed it, so the text now names the applicable triggers — a `pull_request` or `push` whose branch filters match the base or head branch and whose `pull_request` types cover a head update — and says that release, issue, tag-only, scheduled and manual workflows do not hold the card. Reflowed the short lines left by the edit.

## Verified

Synchronous, in-session, foreground only: `scripts/run_tests.sh tests/hermes_cli/test_kanban_pr_acceptance.py tests/hermes_cli/test_kanban_authorized_continuation.py` — raw exit code 0, 18 passed, 0 failed, 21.8 s. Two throwaway runs against reverted source (described above) were used to prove the tests can fail; the working tree was restored from git before the committed run. Nothing was started in the background.

## Checks

Bounded plan: coordinator prechecks (already complete), then this one cleanup pass with the two touched test files, then coordinator postchecks.

- Coordinator prechecks, complete before this session: base lifecycle suite exit 0 (30 tests), current lifecycle suite exit 0 (40 tests), current continuation suite exit 0 (4 tests). Receipts in `core-followup-cleanup`.
- This session: the one targeted two-file run above, raw exit code 0.
- Not run here and not claimed: full suite, lint, typecheck. The repository is not asserted green. A diagnostic-delta comparison is not a raw typecheck, and no raw typecheck exit code exists for this diff.

## Commits

- `7e57ce114d` chore(kanban): clarify workflow-trigger doc and test parameter — doc paragraph, plus the parametrize id `expected` renamed to `completes` so the boolean reads as the completion result rather than as the workflow expectation.
- This report.

## Reverted

none.
