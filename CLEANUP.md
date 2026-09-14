# Cleanup gate report

Run ID: e7667f6f636a42afa5b500436ab732bf
Status: complete

## Passes

One pass over `141718ef11..0f66e08829`: deslop, simplify, test pruning, stale docs. The pass is complete.
The delta lets `hermes -z -` read a literal prompt from stdin. Empty or unreadable stdin exits 2 with a failed usage receipt.

## Rebase

Not needed. The coordinator checked ancestry.

## Removed

- `hermes_cli/oneshot.py`: the two stdin error branches repeated the same receipt, stderr and exit-2 lines.
  One `input_error` path now handles both. Messages, receipts and exit codes are unchanged.

## Tests deleted

None. `test_oneshot_stdin.py` asserts the exact prompt, requested model/provider, stdout and observed receipt.
The empty-stdin test fails if the provider is called or the receipt is not marked failed. Each assertion can fail.
No stale gauntlet test exists here: `tests/test_gauntlet_retention_contract.py` and `news_markets/config.py` are absent from this checkout.

## Docs updated

- `website/docs/reference/cli-commands.md`: the `hermes -z` section now shows `hermes -z - < prompt.txt`.
  The diff changed `-z` help text to add `-`; the reference page did not mention it.

## Verified

- Direct synchronous check with a stubbed `_run_agent` after the edit: exit 0.
  Literal stdin `x $(y) \`z\`` reached the agent verbatim and returned 0.
  Blank stdin returned 2 with `failed: true`. A stdin that raises `OSError` returned 2 with `failed: true`.

## Checks

Bounded plan: `check_runtime.py` runs six oneshot/argparse test files under `scripts/run_tests.sh` (stdin, usage_file, resume, skills, surrogate, argparse flag propagation).
Coordinator prechecks (receipts in `cleanup-runtime-01`, before this pass), raw exit codes:
- base-oneshot-regressions: exit 0, not timed out.
- current-oneshot-regressions: exit 0, not timed out.
No typecheck or lint receipt is in this plan. The full repository is not claimed green.
Postchecks: the coordinator runs them after this pass. They have not run yet.

## Commits

- `21c7524ec0 refactor(oneshot): merge duplicated stdin error paths; document -z -`
- `docs(cleanup): add cleanup gate report for run e7667f6f` (this report only).

## Reverted

none
