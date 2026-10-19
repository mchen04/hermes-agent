from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from hermes_cli import kanban as kanban_cli
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from tests.waiting import wait_until

OPEN_PR = "https://github.com/example/repo/pull/92"


@pytest.fixture
def board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text("kanban: {}\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "orchestrator")
    for var in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_TASK"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    import hermes_cli.profiles as profmod

    monkeypatch.setattr(profmod, "profile_exists", lambda name: True)
    monkeypatch.setattr(kbd, "_memory_pressure_level", lambda: "unknown")
    monkeypatch.setattr(kbd, "_github_guard_pr_state", lambda url: "open")
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    return home


def _blocked_card(*, pr_comment: bool) -> str:
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="release", assignee="forge")
        if pr_comment:
            kb.add_comment(conn, tid, "forge", f"Opened {OPEN_PR}; all checks pass.")
        assert kb.block_task(conn, tid, reason="May I merge?", kind="needs_input")
    return tid


def _unblock(tid: str) -> dict:
    from tools import kanban_tools as kt

    return json.loads(kt._handle_unblock({"task_id": tid}))


def test_unblock_reports_the_respawn_guard_instead_of_a_bare_ready(board) -> None:
    tid = _blocked_card(pr_comment=True)

    out = _unblock(tid)

    assert out["status"] == "ready"
    assert out["worker_spawned"] is False
    assert out["dispatch"]["outcome"] == "respawn_guarded"
    assert out["dispatch"]["reason"] == "active_pr"
    assert out["dispatch"]["open_prs"] == [OPEN_PR]
    assert out["dispatch_text"].startswith("respawn_guarded {'reason': 'active_pr'")
    assert out["dispatch_text"].endswith("no worker spawned")
    with kbc.connect_closing() as conn:
        assert kb.get_task(conn, tid).status == "ready"
        assert not [e for e in kb.list_events(conn, tid) if e.kind in ("claimed", "spawned")]


def test_unblock_of_a_free_card_says_no_worker_has_spawned_yet(board) -> None:
    tid = _blocked_card(pr_comment=False)

    out = _unblock(tid)

    assert (out["worker_spawned"], out["dispatch"]["outcome"]) == (False, "eligible")
    assert out["dispatch_text"] == "eligible: no worker spawned"


def test_unblock_waits_for_the_dispatcher_and_reports_the_real_spawn(board) -> None:
    (board / "config.yaml").write_text("kanban:\n  unblock_wait_seconds: 10\n", encoding="utf-8")
    tid = _blocked_card(pr_comment=False)
    spawned: list = []

    def dispatcher() -> None:
        with kbc.connect_closing() as conn:
            wait_until(lambda: kb.get_task(conn, tid).status == "ready", timeout=10)
            kbd.dispatch_once(conn, spawn_fn=lambda task, ws, board=None: spawned.append(task.id) or 4242)

    thread = threading.Thread(target=dispatcher)
    thread.start()
    out = _unblock(tid)
    thread.join()

    assert spawned == [tid]
    assert out["worker_spawned"] is True
    assert out["dispatch"]["outcome"] == "spawned"
    assert out["dispatch"].get("pid", 4242) == 4242
    assert out["dispatch_text"].endswith("worker spawned")


def test_cli_unblock_prints_what_the_dispatcher_did(board) -> None:
    tid = _blocked_card(pr_comment=True)

    text = kanban_cli.run_slash(f"unblock {tid}")

    assert f"Unblocked {tid}" in text
    assert "dispatcher: respawn_guarded {'reason': 'active_pr'" in text
    assert "no worker spawned" in text
