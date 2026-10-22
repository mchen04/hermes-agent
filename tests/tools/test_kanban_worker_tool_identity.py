"""LOCAL-PATCH kanban-worker-tool-identity: a bound managed worker's own terminal and execute_code
children carry its card label, and nobody else's do.

Real tools (``terminal_tool`` foreground/background, ``spawn_local``, ``execute_code``'s session kernel),
real ``bind_worker_context`` and a real scratch board. A managed worker interpreter runs one frame, so
each interpreter is its own harness process (``_kanban_identity_harness.py``). Every harness starts with
a spoofed ``HERMES_KANBAN_*`` environment: identity must come from the verified binding only.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_connect import connect_closing

HARNESS = Path(__file__).with_name("_kanban_identity_harness.py")
MARKER = "HERMES_DELEGATED_CHILD_CONTEXT"


@pytest.fixture
def board(tmp_path, monkeypatch):
    """``card(title)`` claims a fresh card on a scratch board and returns its managed worker frame."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    path = kb.kanban_db_path()

    def card(title):
        workspace = tmp_path / f"ws-{title}"
        workspace.mkdir()
        with connect_closing(path) as conn:
            task_id = kb.create_task(conn, title=title, assignee="default")
            task = kb.claim_task(conn, task_id, claimer=f"private-host:{title}")
        return {"task_id": task_id, "board": "default", "db": str(path), "run_id": task.current_run_id,
                "claim_lock": task.claim_lock, "workspace": str(workspace), "workspaces_root": str(tmp_path),
                "profile": "default", "branch": None, "tenant": None, "goal_mode": False,
                "max_runtime_seconds": None}

    return card


def _start(tmp_path, name, ops, spoof):
    work = tmp_path / f"work-{name}"
    work.mkdir()
    plan = work / "plan.json"
    plan.write_text(json.dumps({"work": str(work), "ops": ops}), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERMES_KANBAN_") and k != MARKER}
    env.update(TERMINAL_ENV="local", HOME=str(tmp_path), **spoof)
    return subprocess.Popen([sys.executable, str(HARNESS), str(plan)], cwd=work, env=env, text=True, encoding="utf-8",
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _finish(proc):
    out, err = proc.communicate(timeout=300)
    rows = [line for line in out.splitlines() if line.startswith("HARNESS=")]
    assert rows and proc.returncode == 0, (out[-4000:], err[-4000:])
    return json.loads(rows[-1].split("=", 1)[1])


def _spoof(card):
    """A foreign card carrier in the interpreter's own environment (a real card, not its own)."""
    return {"HERMES_KANBAN_TASK": card["task_id"], "HERMES_KANBAN_RUN_ID": str(card["run_id"]),
            "HERMES_KANBAN_CLAIM_LOCK": card["claim_lock"], "HERMES_KANBAN_DB": card["db"]}


def _labeled(row, card):
    return (row["HERMES_KANBAN_TASK"], row["HERMES_KANBAN_RUN_ID"], row["HERMES_KANBAN_CLAIM_LOCK"],
            bool(row[MARKER])) == (card["task_id"], str(card["run_id"]), None, True)


def _unlabeled(row):
    return row["HERMES_KANBAN_TASK"] is None and row["HERMES_KANBAN_RUN_ID"] is None \
        and row["HERMES_KANBAN_CLAIM_LOCK"] is None


def _probe(via, **extra):
    return {"op": "probe", "via": via, **extra}


def test_concurrent_bound_workers_children_carry_only_their_own_card(board, tmp_path):
    """Two managed workers bound at once: each one's terminal (foreground, background, caller env),
    execute_code cell and the program it starts carry that worker's card and run, never the other's,
    never the claim lock, and the fence stays. A shell export of a foreign id does not persist into
    later commands. The label grants nothing: the child cannot complete the card, by tool or CLI, and
    is not the resume guard's dispatcher-owned run."""
    a, b = board("a"), board("b")
    barrier = tmp_path / "barrier"
    barrier.mkdir()

    def ops(own, other, name):
        return [{"op": "bind", "context": own}, {"op": "barrier", "dir": str(barrier), "name": name, "count": 2},
                _probe("terminal"),
                {"op": "shell", "command": f"export HERMES_KANBAN_TASK={other['task_id']} HERMES_KANBAN_RUN_ID=0"},
                _probe("terminal"), _probe("background"),
                _probe("spawn_env", env={"HERMES_KANBAN_TASK": other["task_id"],
                                         "HERMES_KANBAN_RUN_ID": str(other["run_id"])}),
                _probe("kernel"), {"op": "mutate"}]

    procs = [_start(tmp_path, "a", ops(a, b, "a"), _spoof(b)), _start(tmp_path, "b", ops(b, a, "b"), _spoof(a))]
    for card, results in zip((a, b), [_finish(p) for p in procs]):
        bound, _, first, smuggle, after_export, background, caller_env, kernel, mutate = results
        assert bound == {"ok": True} and smuggle == 0, results
        rows = [*first, *after_export, *background, *caller_env, *kernel, *mutate["labels"]]
        assert len(kernel) == 2 and all(_labeled(row, card) for row in rows), (card["task_id"], rows)
        (attempt,) = mutate["mutate"]
        assert "error" in attempt["tool"] and attempt["cli_rc"] != 0, attempt
        assert attempt["resume_exempt"] is False, attempt
    with connect_closing(Path(a["db"])) as conn:
        for card in (a, b):
            task = kb.get_task(conn, card["task_id"])
            assert (task.status, task.current_run_id, task.claim_lock) == ("running", card["run_id"], card["claim_lock"])


def test_no_label_without_a_live_binding_of_this_interpreter(board, tmp_path):
    """Refusals: the unbound front door naming a real card in its env and caller env; a bind with a
    forged claim lock; a bound worker's delegate children and cron runs; and the worker itself once
    its claim is reclaimed or it completed the card — including a terminal session snapshot and an
    execute_code kernel that were spawned while the label was live. (A lapsed lease fails the same
    ``_bound_claim_is_live`` check, pinned in ``tests/agent/test_kanban_turn_recovery.py``.)"""
    owned, completing, forged_target = board("owned"), board("completing"), board("forged")
    front_door = [_probe("terminal"), _probe("background"), _probe("kernel"),
                  _probe("spawn_env", env={"HERMES_KANBAN_TASK": owned["task_id"],
                                           "HERMES_KANBAN_RUN_ID": str(owned["run_id"])})]
    forged = [{"op": "bind", "context": {**forged_target, "claim_lock": owned["claim_lock"]}},
              _probe("terminal"), _probe("kernel")]
    reclaimed = [{"op": "bind", "context": owned},
                 _probe("terminal", scope="delegate"), _probe("kernel", scope="delegate"),
                 _probe("terminal", scope="cron"), _probe("background", scope="cron"),
                 _probe("terminal"), _probe("kernel"),
                 {"op": "reclaim", "db": owned["db"], "task_id": owned["task_id"]},
                 _probe("terminal"), _probe("kernel"), _probe("background")]
    completed = [{"op": "bind", "context": completing}, _probe("terminal"), {"op": "complete"},
                 _probe("terminal"), _probe("kernel")]
    procs = {name: _start(tmp_path, name, ops, _spoof(owned)) for name, ops in (
        ("front", front_door), ("forged", forged), ("reclaimed", reclaimed), ("completed", completed))}
    results = {name: _finish(proc) for name, proc in procs.items()}

    def none_labeled(*probes):
        rows = [row for probe in probes for row in probe]
        return rows and all(_unlabeled(row) for row in rows)

    assert none_labeled(*results["front"]), results["front"]
    forged_bind, *forged_probes = results["forged"]
    assert forged_bind == {"error": "stale_kanban_claim"} and none_labeled(*forged_probes), results["forged"]

    bound, *scoped, live_terminal, live_kernel, reclaim_ok = results["reclaimed"][:8]
    stale = results["reclaimed"][8:]
    assert bound == {"ok": True} and reclaim_ok is True, results["reclaimed"]
    assert none_labeled(*scoped), scoped
    assert all(_labeled(row, owned) for row in [*live_terminal, *live_kernel]), (live_terminal, live_kernel)
    assert none_labeled(*stale), stale

    bound, live, done, *after = results["completed"]
    assert bound == {"ok": True} and done.get("ok") is True, results["completed"]
    assert all(_labeled(row, completing) for row in live) and none_labeled(*after), results["completed"]
