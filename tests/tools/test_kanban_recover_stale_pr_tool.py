"""``kanban_recover_stale_pr``: the worker/orchestrator tool surface for the
explicit stale-``active_pr`` recovery (``recover_stale_pr_guard``).

A dispatcher worker may recover a DIFFERENT unowned card on its own pinned
board (a support brief); never its own card, a live card, another board, or
from a delegate_task child.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

PR5 = "https://github.com/example/repo/pull/5"


@pytest.fixture
def board(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "support")
    for var in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD",
                "HERMES_DELEGATED_CHILD_CONTEXT", "HERMES_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    import hermes_cli.config as cfgmod
    import hermes_cli.profiles as profmod
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_dispatch as kbd
    from tools import kanban_tools as kt

    monkeypatch.setattr(profmod, "profile_exists", lambda name: True)
    monkeypatch.setattr(cfgmod, "load_config", lambda *a, **k: {"kanban": {}})
    monkeypatch.setattr(kt, "load_config", lambda *a, **k: {"kanban": {}})
    monkeypatch.setattr(kbd, "_memory_pressure_level", lambda: "unknown")
    monkeypatch.setattr(kbd, "github_pr_state", lambda url: {
        "url": url, "state": "merged", "merged_at": "2026-10-01T22:09:04Z", "closed_at": None})
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    with kbc.connect() as conn:
        target = kb.create_task(conn, title="nba", assignee="forge")
        kb.add_comment(conn, target, author="forge", body=f"merged {PR5}")
        support = kb.create_task(conn, title="support", assignee="support")
        kb.claim_task(conn, support)
        run_id = kb._current_run_id(conn, support)
    return {"target": target, "support": support, "run_id": run_id, "kb": kb, "kbc": kbc, "kbd": kbd}


@pytest.fixture
def worker(board, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", board["support"])
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(board["run_id"]))
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "default")
    return board


def _call(args):
    from tools.registry import registry
    return json.loads(registry.dispatch("kanban_recover_stale_pr", args))


def _recoveries(b):
    with b["kbc"].connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM task_events WHERE task_id = ? AND kind = 'stale_pr_recovered'",
                            (b["target"],)).fetchone()[0]


def test_real_handler_registered_in_kanban_toolset():
    from tools import kanban_tools as kt
    from tools.registry import registry
    from toolsets import resolve_toolset

    entry = registry.get_entry("kanban_recover_stale_pr")
    assert entry is not None and entry.toolset == "kanban"
    assert entry.handler.__wrapped__ is kt._handle_recover_stale_pr.__wrapped__
    assert entry.check_fn is kt._check_kanban_mode
    props = registry.get_schema("kanban_recover_stale_pr")["parameters"]["properties"]
    assert set(props) == {"task_id", "reason", "dry_run", "spawn", "board"}
    assert "kanban_recover_stale_pr" in resolve_toolset("kanban")


def test_visible_to_worker_hidden_from_delegate_child(worker):
    from agent.delegation_context import delegated_child_context
    from tools.registry import invalidate_check_fn_cache, registry

    def names():
        invalidate_check_fn_cache()
        return {d["function"]["name"] for d in registry.get_definitions({"kanban_recover_stale_pr"}, quiet=True)}

    assert names() == {"kanban_recover_stale_pr"}
    with delegated_child_context():
        assert names() == set()


def test_worker_recovers_different_unowned_card_and_spawns_one(worker, monkeypatch):
    kbd = worker["kbd"]
    spawned = []
    monkeypatch.setattr(kbd, "_default_spawn", lambda task, ws, board=None: spawned.append(task.id))
    out = _call({"task_id": worker["target"], "reason": "PR merged; human said resume", "spawn": True})
    assert out["ok"] and out["status"] == "recorded" and out["spawned"] == [worker["target"]]
    assert spawned == [worker["target"]] and _recoveries(worker) == 1
    with worker["kbc"].connect() as conn:
        assert conn.execute("SELECT status FROM tasks WHERE id = ?", (worker["target"],)).fetchone()[0] == "running"
        # The worker's own card is untouched.
        assert conn.execute("SELECT status FROM tasks WHERE id = ?", (worker["support"],)).fetchone()[0] == "running"
    # Now live: a repeat is refused, nothing more spawns.
    again = _call({"task_id": worker["target"], "spawn": True})
    assert "refused (not_ready)" in again["error"] and spawned == [worker["target"]]


def test_dry_run_records_and_spawns_nothing(worker, monkeypatch):
    monkeypatch.setattr(worker["kbd"], "_default_spawn", lambda *a, **k: pytest.fail("must not spawn"))
    out = _call({"task_id": worker["target"], "dry_run": True, "spawn": True})
    assert out["ok"] and out["status"] == "verified" and out["prs"][0]["state"] == "merged"
    assert "spawned" not in out and _recoveries(worker) == 0


def test_open_pr_refused_with_evidence(worker, monkeypatch):
    monkeypatch.setattr(worker["kbd"], "github_pr_state", lambda url: {"state": "open"})
    out = _call({"task_id": worker["target"], "spawn": True})
    assert "refused (pr_not_closed)" in out["error"] and out["prs"][0]["state"] == "open"
    assert _recoveries(worker) == 0


def test_worker_cannot_target_its_own_or_a_live_card(worker):
    assert "your own live card" in _call({"task_id": worker["support"]})["error"]
    with worker["kbc"].connect() as conn:
        assert worker["kb"].claim_task(conn, worker["target"]) is not None
    assert "refused (not_ready)" in _call({"task_id": worker["target"]})["error"]
    assert _recoveries(worker) == 0


def test_task_id_is_required_never_defaulted_from_env(worker):
    assert "task_id is required" in _call({})["error"]


def test_delegate_child_denied(worker, monkeypatch):
    from agent.delegation_context import DELEGATED_CHILD_ENV_MARKER

    monkeypatch.setenv(DELEGATED_CHILD_ENV_MARKER, "1")
    out = _call({"task_id": worker["target"]})
    assert "delegate_task child agents are not Kanban run owners" in out["error"]
    monkeypatch.delenv(DELEGATED_CHILD_ENV_MARKER)
    assert _recoveries(worker) == 0


def test_worker_pinned_to_its_board(worker):
    out = _call({"task_id": worker["target"], "board": "other-board"})
    assert "pinned to board 'default'" in out["error"]
    assert _call({"task_id": worker["target"], "board": "default"})["status"] == "recorded"


def test_orchestrator_without_env_task_may_use_it(board):
    assert _call({"task_id": board["target"]})["status"] == "recorded"


def test_unknown_parameter_rejected(worker):
    assert "unknown parameter(s): force" in _call({"task_id": worker["target"], "force": True})["error"]
    assert _recoveries(worker) == 0


def test_spawn_from_worker_does_not_leak_worker_identity(worker, monkeypatch):
    """The real ``_default_spawn`` run inside a worker must hand the new worker
    only its own task identity."""
    import subprocess

    kbd = worker["kbd"]
    for var, value in (("HERMES_KANBAN_BRANCH", "wt/support"), ("HERMES_KANBAN_GOAL_MODE", "1"),
                       ("HERMES_KANBAN_GOAL_MAX_TURNS", "9"), ("HERMES_TENANT", "support-tenant"),
                       ("HERMES_SESSION_ID", "support-session"), ("HERMES_KANBAN_WORKSPACE", "/tmp/support")):
        monkeypatch.setenv(var, value)
    captured = {}

    class FakeProc:
        pid = 4242

    def fake_popen(cmd, **kw):
        captured.update(kw["env"])
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(kbd, "_set_worker_pid", lambda *a, **k: None)
    out = _call({"task_id": worker["target"], "spawn": True})
    assert out["spawned"] == [worker["target"]], out
    assert captured["HERMES_KANBAN_TASK"] == worker["target"]
    assert captured["HERMES_KANBAN_RUN_ID"] != str(worker["run_id"])
    for leaked in ("HERMES_KANBAN_BRANCH", "HERMES_KANBAN_GOAL_MODE", "HERMES_KANBAN_GOAL_MAX_TURNS",
                   "HERMES_TENANT", "HERMES_SESSION_ID"):
        assert leaked not in captured, leaked
    assert captured.get("HERMES_KANBAN_WORKSPACE") != "/tmp/support"
