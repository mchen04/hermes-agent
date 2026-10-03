"""Single-card dispatch (``dispatch_task``) and the worker-scoped spawn env.

``dispatch_task`` starts one named ready card through the normal dispatch path:
the board lock, caps, respawn guards and the claim CAS all apply, and no other
card is touched. A worker spawned from inside another worker gets only its own
task identity.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd

PR5 = "https://github.com/example/repo/pull/5"


@pytest.fixture
def kanban_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from hermes_cli import config as cfgmod
    from hermes_cli import profiles as profmod

    monkeypatch.setattr(profmod, "profile_exists", lambda name: True)
    monkeypatch.setattr(cfgmod, "load_config", lambda *a, **k: {"kanban": {}})
    monkeypatch.setattr(kbd, "_memory_pressure_level", lambda: "unknown")
    kb.init_db()
    return home


def _guarded_card(conn, assignee="forge"):
    tid = kb.create_task(conn, title="nba", assignee=assignee)
    kb.add_comment(conn, tid, author="forge", body=f"PR #5 opened: {PR5}")
    assert kbd.check_respawn_guard(conn, tid) == "active_pr"
    return tid


def test_dispatch_task_spawns_exactly_the_named_card_once(kanban_home):
    spawned = []
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="named", assignee="forge")
        other = kb.create_task(conn, title="unrelated ready card", assignee="portfolio")

        def spawn(task, workspace):
            spawned.append(task.id)

        assert [s[0] for s in kbd.dispatch_task(conn, tid, spawn_fn=spawn).spawned] == [tid]
        assert kbd.dispatch_task(conn, tid, spawn_fn=spawn).spawned == []
        assert kb.claim_task(conn, tid) is None
        assert spawned == [tid]
        row = conn.execute("SELECT status, current_run_id FROM tasks WHERE id = ?", (tid,)).fetchone()
        assert row["status"] == "running" and row["current_run_id"] is not None
        assert conn.execute("SELECT status, claim_lock FROM tasks WHERE id = ?", (other,)).fetchone()[:] == (
            "ready", None)


def test_dispatch_task_keeps_guards_caps_and_board_lock(kanban_home):
    spawn = lambda task, workspace: None
    with kbc.connect() as conn:
        held = _guarded_card(conn)
        res = kbd.dispatch_task(conn, held, spawn_fn=spawn)
        assert res.spawned == [] and res.respawn_guarded == [(held, "active_pr")]

        tid = kb.create_task(conn, title="free", assignee="forge")
        busy = kb.create_task(conn, title="busy", assignee="forge")
        assert kb.claim_task(conn, busy) is not None
        assert kbd.dispatch_task(conn, tid, spawn_fn=spawn, max_spawn=1).spawned == []
        res = kbd.dispatch_task(conn, tid, spawn_fn=spawn, max_in_progress_per_profile=1)
        assert res.spawned == [] and res.skipped_per_profile_capped == [(tid, "forge", 1)]

        with kbc._dispatch_tick_lock(kb.kanban_db_path()) as lock_held:
            assert lock_held
            assert kbd.dispatch_task(conn, tid, spawn_fn=spawn).skipped_locked
        assert conn.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()[0] == "ready"


def test_held_card_records_its_guard_once_per_hold_not_per_tick(kanban_home):
    spawn = lambda task, workspace: None
    with kbc.connect() as conn:
        tid = _guarded_card(conn)

        def guard_events():
            return conn.execute("SELECT COUNT(*) FROM task_events WHERE task_id = ? AND kind = 'respawn_guarded'",
                                (tid,)).fetchone()[0]

        for _ in range(3):
            assert kbd.dispatch_task(conn, tid, spawn_fn=spawn).respawn_guarded == [(tid, "active_pr")]
        assert guard_events() == 1
        # Something else happened on the card, so the next held tick records the hold again.
        kb.add_comment(conn, tid, author="michael", body="still waiting")
        kbd.dispatch_task(conn, tid, spawn_fn=spawn)
        kbd.dispatch_task(conn, tid, spawn_fn=spawn)
        assert guard_events() == 2

def test_dispatch_task_respects_dependencies(kanban_home):
    with kbc.connect() as conn:
        parent = kb.create_task(conn, title="parent", assignee="forge")
        child = kb.create_task(conn, title="child", assignee="forge", parents=[parent])
        assert conn.execute("SELECT status FROM tasks WHERE id = ?", (child,)).fetchone()[0] == "todo"
        assert kbd.dispatch_task(conn, child, spawn_fn=lambda t, w: None).spawned == []


def test_spawn_from_inside_a_worker_does_not_leak_its_identity(kanban_home, monkeypatch):
    import subprocess

    support_workspace = str(kanban_home / "support")
    for var, value in (("HERMES_KANBAN_TASK", "t_support"), ("HERMES_KANBAN_RUN_ID", "999"),
                       ("HERMES_KANBAN_CLAIM_LOCK", "support-lock"), ("HERMES_KANBAN_BRANCH", "wt/support"),
                       ("HERMES_KANBAN_GOAL_MODE", "1"), ("HERMES_KANBAN_GOAL_MAX_TURNS", "9"),
                       ("HERMES_TENANT", "support-tenant"), ("HERMES_SESSION_ID", "support-session"),
                       ("HERMES_KANBAN_WORKSPACE", support_workspace)):
        monkeypatch.setenv(var, value)
    captured = {}

    class FakeProc:
        pid = 4242

    def fake_popen(cmd, **kw):
        captured.update(kw["env"])
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(kbd, "_set_worker_pid", lambda *a, **k: None)
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="target", assignee="forge")
        assert [s[0] for s in kbd.dispatch_task(conn, tid).spawned] == [tid]
    assert captured["HERMES_KANBAN_TASK"] == tid
    assert captured["HERMES_KANBAN_RUN_ID"] != "999"
    assert captured.get("HERMES_KANBAN_CLAIM_LOCK") != "support-lock"
    assert captured.get("HERMES_KANBAN_WORKSPACE") != support_workspace
    for leaked in ("HERMES_KANBAN_BRANCH", "HERMES_KANBAN_GOAL_MODE", "HERMES_KANBAN_GOAL_MAX_TURNS",
                   "HERMES_TENANT", "HERMES_SESSION_ID"):
        assert leaked not in captured, leaked
