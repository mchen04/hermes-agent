# Cleanup gate — answered-unblock watermark / applicable-CI follow-up

Run ID: 394f378c26214ce89c41b83f6b6c4c81
Status: complete

Base: `b3ff391428956ae3b213451d356a11150247b2bf`, branch `fix/forge-continuation-20260921`.

## Passes

One pass, as bounded, over the five-file follow-up diff. The pass ran in the preceding session, which committed its scoped change before the CLI missed its exit deadline. Those changes are preserved and were re-read here line by line: `_expects_head_checks` and `_matches_ref` in `kanban_pr_acceptance.py`, the watermark binding in `kanban_db.py`, the guard in `kanban_recovery.py`. No dead branch, silenced type or defensive filler remains. Every guard is reachable: a lone `[` or a leading `?`/`+` reaches the `ValueError`, and the `continue` after a stale watermark is load-bearing. No second pass, no unrelated rework.

## Rebase

Not needed; the coordinator checked ancestry. No push, fetch, rebase, reset or clean was run.

## Removed

Nothing in the source diff. One simplification was drafted for the `pull_request` `types` check and dropped before commit: collapsing the nested `if` needed a defaulted sentinel tuple that read worse than the two plain lines it replaced.

## Tests deleted

none. Both new tests can fail. `test_new_pr_comment_after_answered_unblock_keeps_guard` was checked against reverted source: the answered `auto_resumed` event then reads as legacy permission, the guard returns `None`, and the test fails. Each of the ten `test_unrelated_workflows_do_not_require_pr_checks` cases asserts a distinct trigger shape against a real completion result.

Known gap, not a deletion: the two source hunks are defence in depth. Reverting only the `kanban_db.py` watermark binding leaves the continuation test green, because `kanban_recovery.py` alone suppresses the legacy path. No test isolates that hunk; adding one is outside this bounded pass.

## Docs updated

`website/docs/user-guide/features/kanban.md`, acceptance paragraph. "An active workflow that can trigger on it holds the card as pending" described the old, broad rule this diff narrowed. The text now names the applicable triggers and says release, issue, tag-only, scheduled and manual workflows do not hold the card. No other document repeats the old wording.

## Verified

No tests were run in this session; the coordinator's checks are the oracle. The pass's own verification, in the preceding session, was a synchronous foreground run of the two touched test files via `scripts/run_tests.sh` — raw exit code 0, 18 passed. Nothing was started in the background here.

## Checks

Bounded plan: coordinator prechecks, one cleanup pass over the five-file diff, then coordinator postchecks.

- Prechecks (coordinator, complete before this session; receipts in `core-followup-receipt`): base lifecycle raw exit 0, current lifecycle raw exit 0, current continuation raw exit 0.
- Postchecks (coordinator): not run yet; they run after this session exits.
- Not run and not claimed: full suite, lint, raw typecheck. The repository is not asserted green. A diagnostic-delta check is not a raw typecheck, and no raw typecheck exit code exists for this diff.

## Commits

- `7e57ce114d` chore(kanban): clarify workflow-trigger doc and test parameter.
- This report.

## Reverted

none.
