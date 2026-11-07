from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from gateway.drain_control import RESTART_WAIT_REASON
from gateway.session_managed_worker import _record_lost_attempt
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli.kanban_dispatch_pr_guard import respawn_guard_verdict
from hermes_cli.kanban_worker_wake import RESTART_LINE, build_worker_wake
from hermes_cli.plugins import get_plugin_manager
from hermes_state_runtime import RuntimeStoreError

NOW = int(time.time())
HOST = kb._claimer_id().split(":", 1)[0]
FINGERPRINT = "6a232414-2dbf-40ab-a534-d9b71db05658:116|4162240"


def _absent_pid(base: int = 3_800_000) -> int:
    pid = base
    while psutil.pid_exists(pid):
        pid += 1
    return pid


@pytest.fixture
def board(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_CRASH_GRACE_SECONDS", "0")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    conn = kbc.connect()
    ledger = tmp_path / "state.db"
    with sqlite3.connect(ledger) as store:
        store.execute("CREATE TABLE session_admissions (target_session_id TEXT, request_id TEXT, status TEXT, outcome TEXT)")
    yield _Board(conn, ledger)
    conn.close()


@pytest.fixture
def exited_hooks(monkeypatch):
    mgr = get_plugin_manager()
    events: list[dict] = []
    saved = {k: list(v) for k, v in mgr._hooks.items()}
    mgr._hooks.setdefault("on_kanban_worker_exited", []).append(lambda **kw: events.append(kw))
    try:
        yield events
    finally:
        mgr._hooks = saved


class _Board:
    def __init__(self, conn, ledger):
        self.conn = conn
        self.ledger = ledger

    def running(self, tid: str, *, completed_before: bool = False) -> int:
        worker_pid = _absent_pid()
        lock = f"{HOST}:{_absent_pid(base=3_900_000)}"
        started = NOW - 120
        self.conn.execute(
            "INSERT INTO tasks (id, title, assignee, status, created_at, claim_lock, claim_expires, worker_pid, "
            "worker_started_at, last_heartbeat_at, started_at, priority) VALUES (?, ?, 'forge', 'running', ?, ?, ?, ?, ?, ?, ?, 5)",
            (tid, f"card {tid}", NOW - 86400, lock, NOW + 900, worker_pid, FINGERPRINT, NOW - 30, started))
        if completed_before:
            self.conn.execute(
                "INSERT INTO task_runs (task_id, profile, status, outcome, started_at, ended_at) "
                "VALUES (?, 'forge', 'completed', 'completed', ?, ?)", (tid, NOW - 7200, NOW - 3600))
        run_id = int(self.conn.execute(
            "INSERT INTO task_runs (task_id, profile, status, claim_lock, claim_expires, worker_pid, started_at) "
            "VALUES (?, 'forge', 'running', ?, ?, ?, ?)", (tid, lock, NOW + 900, worker_pid, started)).lastrowid)
        self.conn.execute("UPDATE tasks SET current_run_id = ? WHERE id = ?", (run_id, tid))
        request_id = f'kanban:["default","{tid}",{run_id}]'
        for kind, payload in (("claimed", {"lock": lock, "expires": NOW + 900}),
                              ("spawned", {"pid": worker_pid, "started_at": FINGERPRINT}),
                              ("owner_admitted", {"db": str(self.ledger), "session_id": f"sess-{tid}",
                                                  "pid": _absent_pid(base=3_950_000), "request_id": request_id})):
            self.conn.execute("INSERT INTO task_events (task_id, run_id, kind, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                              (tid, run_id, kind, json.dumps(payload), started))
        with sqlite3.connect(self.ledger) as store:
            store.execute("INSERT INTO session_admissions VALUES (?, ?, 'terminal', 'interrupted')", (f"sess-{tid}", request_id))
        self.conn.commit()
        return run_id

    def state(self, tid: str) -> dict:
        return dict(self.conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone())

    def run(self, run_id: int) -> dict:
        return dict(self.conn.execute("SELECT * FROM task_runs WHERE id = ?", (run_id,)).fetchone())

    def kinds(self, tid: str, run_id: int) -> list[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT kind FROM task_events WHERE task_id = ? AND run_id = ? ORDER BY id", (tid, run_id))]

    def policy(self, tid: str, run_id: int):
        return SimpleNamespace(kanban_json=json.dumps({
            "task_id": tid, "db": str(kb.kanban_db_path()), "run_id": run_id,
            "claim_lock": self.state(tid)["claim_lock"], "board": None}))

    def tick(self) -> kbd.DispatchResult:
        return kbd.dispatch_once(self.conn, max_spawn=0, failure_limit=2, stale_timeout_seconds=0)


def _gateway(*, restart_wait: bool):
    runner = SimpleNamespace(_draining=restart_wait, _running=True,
                             session_runtime_descriptor={"drain_reason": RESTART_WAIT_REASON if restart_wait else None})
    return SimpleNamespace(runner=runner)


async def _lose_worker(board, tid, run_id, *, restart_wait):
    recording = _record_lost_attempt(_gateway(restart_wait=restart_wait), board.policy(tid, run_id),
                                     RuntimeStoreError("managed_worker_lost"), "managed_worker_lost", None)
    assert recording is not None
    await recording


@pytest.mark.asyncio
async def test_a_worker_lost_during_a_restart_wait_is_interrupted_and_respawns_at_once(board, exited_hooks):
    run_id = board.running("t_restart", completed_before=True)
    await _lose_worker(board, "t_restart", run_id, restart_wait=True)

    board.tick()

    row, run = board.state("t_restart"), board.run(run_id)
    assert row["status"] == "ready"
    assert (row["claim_lock"], row["worker_pid"], row["current_run_id"]) == (None, None, None)
    assert run["outcome"] == "interrupted" and run["error"] == "interrupted by a gateway restart"
    assert row["consecutive_failures"] == 0 and row["last_failure_error"] is None
    assert "interrupted" in board.kinds("t_restart", run_id)
    assert not {"crashed", "gave_up", "protocol_violation"} & set(board.kinds("t_restart", run_id))
    assert respawn_guard_verdict(board.conn, "t_restart") == (None, {})
    assert [(e["outcome"], e["run_id"]) for e in exited_hooks] == [("interrupted", run_id)]

    task = kb.claim_task(board.conn, "t_restart")
    wake = build_worker_wake(board.conn, task, {"enabled": True})
    assert wake.splitlines()[0] == RESTART_LINE
    assert f"Your previous run {run_id} ended interrupted" in wake


@pytest.mark.asyncio
async def test_the_same_loss_outside_a_restart_wait_still_books_a_crash(board, exited_hooks):
    run_id = board.running("t_plain")
    await _lose_worker(board, "t_plain", run_id, restart_wait=False)

    board.tick()

    row, run = board.state("t_plain"), board.run(run_id)
    assert row["status"] == "ready"
    assert run["outcome"] == "crashed" and "managed worker lost: managed_worker_lost" in run["error"]
    assert row["consecutive_failures"] == 1 and row["last_failure_error"]
    assert [(e["outcome"], e["run_id"]) for e in exited_hooks] == [("crashed", run_id)]


@pytest.mark.asyncio
async def test_many_interrupted_cards_in_one_tick_never_trip_the_systemic_breaker(board):
    runs = {tid: board.running(tid) for tid in ("t_a", "t_b", "t_c")}
    for tid, run_id in runs.items():
        await _lose_worker(board, tid, run_id, restart_wait=True)

    result = board.tick()

    assert result.crashed == [] and result.auto_blocked == []
    assert {board.state(tid)["status"] for tid in runs} == {"ready"}
    assert {board.state(tid)["consecutive_failures"] for tid in runs} == {0}
