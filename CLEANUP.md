# Cleanup gate — kanban continuation / unprotected-CI diff

Run ID: 20517401ab6747d3bf3572c4732dcbba
Status: complete

Base: `870714c51b02076cfd077830ebc61cee04afbb13`, branch `fix/forge-continuation-20260921`.

## Passes

One pass, as bounded, over the 7-file diff against the base. No unrelated file was touched, and no already-clear code was rewritten for style.

## Rebase

Not needed; the coordinator checked ancestry. No rebase, fetch, reset or clean.

## Removed

- `kanban_pr_acceptance.py`: dead `"kind": "check-suite"` key on synthesized suite checks. Nothing reads it — `_classify` uses `head_sha`/`status`/`conclusion`, and the receipt copies only `name`, `id`, `url`, `head_sha`, `classification`, `conclusion`.
- `kanban_pr_acceptance.py`: `_unreported_app_suite` ran twice per suite across two comprehensions. The placeholder id set is computed once, and both the receipt list and `selected_checks` partition on it, so the reported/unreported split has one source of truth.

Kept on purpose: the function-local `authorized_pr_continuation` import in `kanban_db_dispatch.py` (the module's existing cycle-breaking convention), the `LOCAL-PATCH` markers, and the fail-closed `"missing"` classification default.

## Tests deleted

none. Every assertion in both touched test files can fail: the `block_task` / `unblock_task` / `complete_task` asserts check real return values, the respawn-guard asserts separate `None` from `active_pr`, and `receipt["unreported_suites"][0]` raises if the placeholder predicate stops matching.

## Docs updated

`website/docs/user-guide/features/kanban.md`:

- The `active_pr` reference listed only handoffs as lifts and required a different-profile change. The diff added the unblock lift, so it now states that an explicit unblock, or an auto-resume answering a `needs_input` block, lifts the guard without a profile change — and that a timer auto-resume, crash or reclaim does not, and a later PR comment re-guards.
- Reflowed the mangled short line in the acceptance paragraph this diff rewrote.

## Verified

Synchronous, in-session: `scripts/run_tests.sh tests/hermes_cli/test_kanban_pr_acceptance.py` — raw exit code 0, 4 passed, 0 failed, 10.9 s. That is the test file for the module I edited. Nothing ran in the background.

## Checks

Bounded plan: coordinator prechecks, then this one cleanup pass with one targeted test file, then coordinator postchecks.

Prechecks already ran (receipts in `Documents/Hermes-Forge-Fixes-2026-09-21/core-cleanup-final`): base lifecycle + acceptance raw exit 0, current lifecycle + acceptance raw exit 0, current authorized-continuation raw exit 0.

Postchecks have NOT run — full suite, lint and typecheck belong to the coordinator after I exit. No raw typecheck ran here, no diagnostic-delta result is offered as one, and nothing here claims the repository is green beyond the single test file above.

## Commits

One commit, titled `chore(kanban): dedupe suite partition and refresh guard docs`. It carries the code edit, the doc edit and this report.

## Reverted

none
