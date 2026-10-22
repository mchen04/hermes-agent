# WORKLOG — t_6c93ea03 worker identity repair

Branch `fix/worker-identity-t_6c93ea03`, base `64fb8fdf41` (local/upstream-patches). LOCAL-PATCH name:
`kanban-worker-tool-identity`. All existing local patches untouched.

## Problem (reproduced)

Under managed exec the dispatcher's spawned `kanban_worker_client` is the run's `worker_pid`. The agent
runs in a private interpreter (`agent.managed_worker`) that `bind_worker_context` verifies and binds.
The upstream descendant fence (`delegated_child_subprocess_env` → `scrub_kanban_env`) strips
`HERMES_KANBAN_TASK`/`RUN_ID` from every terminal and execute_code child. So:

- the worker's shell has no `$HERMES_KANBAN_TASK` (the KANBAN_GUIDANCE `wt/$HERMES_KANBAN_TASK` step
  breaks), and
- the supervise skill's `cardsteer.refusal()` refuses the worker for its own card's coder. Its
  fallback (`tasks.worker_pid` in the caller's ancestry) names the submitter, not an ancestor.

Reproduced from a scratch board (the demo below, base column): a bound worker's terminal child sees
`TASK=None`, and cardsteer refuses its own card.

## Implementation

The identity comes from the verified binding only, never from an environment variable.

- `gateway/session_kanban.bind_worker_context`: after its claim check and `worker_bound` event, records
  the card's `{TASK, RUN_ID}` plus a liveness re-proof (`_bound_claim_is_live`: task running, same run,
  same claim lock, unexpired lease, open run, and `worker_bound` pid == this interpreter).
- `agent/delegation_context`: `bind_worker_identity`, `worker_tool_label()` (the label, or `{}` when
  unbound, not the dispatcher-owned context (delegate child, cron, fenced descendant), or the claim is
  not live), and `worker_tool_env(env)` (re-scrub, then add the label; the claim lock never travels and
  the fence marker stays).
- Applied at the three worker-own transports, after their existing scrub:
  - `tools/environments/local._make_run_env` (foreground terminal, search);
  - `tools/process_registry._spawn_env` (background terminal, `spawn_local`);
  - `tools/code_execution_env._build_child_env` (execute_code, both local paths).
- `tools/code_kernel`: the session kernel's env is frozen at spawn, so the label is part of the kernel
  key. A stale claim gets a fresh unlabeled kernel on the next cell.
- `tools/environments/base_session_env`: `HERMES_KANBAN_TASK`/`RUN_ID` are unset before the snapshot
  `export -p`. A persisted value would override the per-spawn label on every later `source`: a
  foreign id exported by one command, or the old card after the claim went stale.
- `hermes_cli/kanban_resume_guard._dispatcher_owned_run`: also requires the dispatcher-owned context,
  so a labeled (fenced) child is never the exempt dispatcher-owned run.
- Docs: `website/docs/user-guide/features/kanban-worker-lanes.md` (Descendant process scope).

Unchanged on purpose:
- ownership checks;
- the fence (labeled children still can't mutate the board by tool or CLI);
- cardsteer (external);
- every delegate, cron and front-door path.

## Changed files

agent/delegation_context.py, gateway/session_kanban.py, hermes_cli/kanban_resume_guard.py,
tools/code_execution_env.py, tools/code_kernel.py, tools/environments/base_session_env.py,
tools/environments/local.py, tools/process_registry.py, website/docs/.../kanban-worker-lanes.md,
tests/tools/test_kanban_worker_tool_identity.py (new), tests/tools/_kanban_identity_harness.py (new),
tests/gateway/fixtures/kanban_owner_probe.py + tests/gateway/test_session_kanban.py (real-daemon
terminal identity check), WORKLOG.md.

## Tests

`tests/tools/test_kanban_worker_tool_identity.py` uses the real tools, the real `bind_worker_context`
and a scratch board. Each interpreter is a separate harness process that starts with a spoofed
`HERMES_KANBAN_*` env.

1. `test_concurrent_bound_workers_children_carry_only_their_own_card`: two workers are bound at once
   (barrier). Each one's children carry its own card+run, never the other's, no claim lock, fence
   set. This holds for foreground and background terminal, `spawn_local` with a caller env naming the
   other card, and an execute_code cell plus a program it starts. A shell `export` of the other id
   does not reach later commands. A labeled child still can't complete its card (tool error, CLI
   rc≠0), and the resume guard doesn't exempt it. Both cards are unchanged afterwards.
2. `test_no_label_without_a_live_binding_of_this_interpreter`: no label for
   - the front door naming a real card in its env and in a caller env;
   - a bind with another card's claim lock (refused: `stale_kanban_claim`);
   - delegate children and cron runs of a bound worker;
   - the worker after reclaim, after the lease lapses, and after it completes the card. This
     includes a terminal snapshot and a kernel that were labeled while the claim was live.
3. `test_real_dispatcher_lifecycle[complete]` (existing real-daemon probe; dispatcher → submitter →
   owner → managed interpreter): the loopback model's first turn also calls `terminal`. The worker's
   shell must print `IDENTITY=<card>:<run>:none` (no claim lock). This applies only to the owner
   probe: the recovery probe's `Model` subclass leaves `server.identity` unset.

## Commands and results

Test interpreter (local scratch runs):
`HERMES_PYTHON=~/.hermes/installs/ba26f32046407286/test-environment/gen-1d095ed2a4c24af9b67ab06e0c12cd0c/venv/bin/python`.
Exports:
- `~/scratch/t_6c93ea03-worker-identity/fix`: worktree files via `git ls-files -co`, plus `git init`;
- `.../basecheck`: `git archive HEAD` plus the two new test files.
mbp-main:
- `~/scratch/t_6c93ea03-worker-identity/{base,fix}` with a disposable venv built by
  `python3 -m pm.build_env --extra all --group dev --group test`;
- `HERMES_HOME` is scratch.

All test runs use `bash scripts/run_tests.sh` in an export. mbp-main runs used `ulimit -n 8192`: its
non-interactive default of 256 made `test_api_server`/`test_tui_gateway_server` die with EMFILE.

| Run | Where | Result |
|---|---|---|
| new tests, fix | mbp-old fix export | 2 passed |
| new tests, base + new tests | mbp-old basecheck | 2 failed: bound worker's children `TASK=None` |
| mutations (9, one per hunk) | mbp-old fix export | 8 killed (each fails ≥1 test); the survivor is the deliberate defensive re-scrub in `worker_tool_env` (call sites already scrub) |
| real-daemon probe `test_real_dispatcher_lifecycle[complete]` + new terminal check | mbp-main | base `IDENTITY=::none` → fail; fix → pass |
| 319 files touching changed modules (`grep` selection) | mbp-main fix | 3975 passed, 4 failed, 203 skipped |
| the failing files rerun, base vs fix | mbp-main | the same 3 fail on base and fix (`test_session_client_env` ×2: scratch venv first on PATH; `test_kanban_db::test_infrastructure_spawn_refusal_never_charges_the_card`). api_server/tui_gateway pass with the fd limit raised |
| hermes-local `CORE_TESTS` + new test + `test_kanban_recovery.py` | mbp-main fix | 580 passed, 0 failed, 4 skipped (31 s) |
| final focused rerun after lint fixes | mbp-old + mbp-main | 9 passed; 37 passed, 3 skipped |
| `ruff check --preview`, `check-windows-footguns`, `check_subprocess_stdin`, `check_no_tmp_literals` on changed files | mbp-old | clean (2 pre-existing footguns in `test_session_kanban.py` lines 22/69 untouched) |

Scratch cardsteer demo (not committed; `~/scratch/t_6c93ea03-worker-identity/tools/cardsteer_demo.py`):
it runs the real `~/.claude/skills/supervise/scripts/cardsteer.py` `refusal('<card>-coder')` from a
`terminal_tool` child. The scratch board's `worker_pid` is a live non-ancestor, standing in for the
submitter.

| | bound worker | front door naming the card in its env |
|---|---|---|
| base | `TASK=None`, refused | `TASK=None`, refused |
| fix | `TASK=<card>`, allowed | `TASK=None`, refused |

## Activation (Forge)

1. Install this commit onto `local/upstream-patches` (it applies on `64fb8fdf41`).
2. Add `tests/tools/test_kanban_worker_tool_identity.py` to `CORE_TESTS` in
   `~/projects/Tools/hermes-local/scripts/check_runtime.py`. The runtime proof runs it; it takes about
   6–14 s.
3. The fix acts inside each managed worker interpreter. The owner gateway spawns a fresh
   `sys.executable -m agent.managed_worker` per frame and binds the card there; the gateway process
   itself is unbound and needs none of this code. A worker started after the source update gets the
   fix. One already running keeps the old code until its interpreter ends. Not verified here: whether
   the running gateway's new interpreters import the updated tree. That depends on how Forge installs
   (in-place source update vs a new versioned source dir). If it pins a versioned dir, a gateway
   restart is needed, and only when coordinated with other live cards. I did not restart anything.
4. Resume Router (`t_8fa46a77-michael-router`, UUID `3c37653a-f3e5-4da3-9837-8b44d3d504fa`, settings
   unchanged) only after the install is verified. To check: a dispatched worker's terminal
   `echo $HERMES_KANBAN_TASK` prints its card, and `cardsteer.py` accepts that worker for its own coder.

## Not covered / known limits

- Remote terminal backends (docker/ssh/modal) and remote execute_code (`_execute_remote`) build their
  env elsewhere and carry no label. Neither do Codex app-server native shells.
- Legacy env-spawned workers (pre-managed-exec, no `bind_worker_context`) get no shell label. Their
  cardsteer still works through `worker_pid` ancestry.
- A background process keeps the label it was spawned with. It doesn't lose it if the claim later
  goes stale; the kernel and the foreground terminal do re-check. Reclaim kills the submitter, and
  `_watch_submitter` then ends the interpreter.
- The label is cooperative scoping, like the fence. A shell can still set any variable for its own
  command; Hermes gates never trust a labeled (fenced) child.

## round_analysis

Round 1:
- Reproduced at the real boundary: the daemon probe shows `IDENTITY=::none` and the cardsteer demo
  refuses the worker its own card.
- Fixed: own-card label in terminal (fg/bg), `spawn_local` and execute_code, plus three gaps the
  label opened that are now closed:
  - a stale label persisting through the shell snapshot;
  - a stale label in a kernel frozen at spawn;
  - the resume guard exempting a labeled child.
  Each gap has a mutation-killing test.
- Measured gain: base red → fix green on both new tests and the real-daemon probe. No regressions
  across 319 related files; CORE_TESTS green.

Stop counter: 0. This round fixed a reproduced bug. A further round has no known reproduced bug to
target; the remaining items under "Not covered" are scope notes, not reproduced failures.
