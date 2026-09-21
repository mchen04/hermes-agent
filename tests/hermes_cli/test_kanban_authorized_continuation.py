"""A new continuation dispatches the same task once; a timer is not permission."""
from pathlib import Path
import subprocess
import sys

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_dispatch as dispatch
from hermes_cli.kanban_db_connect import connect


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda name: True)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda *a, **k: {})
    kb.init_db()
    with connect() as conn:
        yield conn


@pytest.mark.parametrize("resume", [None, {"trigger": "answered", "kind": "needs_input"}])
def test_authorized_continuation_overrides_old_pr_without_duplicate_dispatch(board, resume):
    tid = kb.create_task(board, title="Continue local verification", assignee="forge")
    kb.add_comment(board, tid, "forge", "PR https://github.com/example/app/pull/7 is closed.")
    assert kb.block_task(board, tid, reason="Continue locally?", kind="needs_input")
    assert kb.unblock_task(board, tid, auto_resume=resume)
    # Events can land in the same second: ordering must still preserve the answer.
    assert dispatch.check_respawn_guard(board, tid) is None
    children = []
    def spawn(*args, **kwargs):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        children.append(child)
        return child.pid
    try:
        result = dispatch.dispatch_once(board, spawn_fn=spawn)
        assert [row[0] for row in result.spawned].count(tid) == 1
        assert not dispatch.dispatch_once(board, spawn_fn=spawn).spawned
        assert len(children) == 1
    finally:
        for child in children:
            child.terminate()
            child.wait(timeout=5)


def test_timer_and_newer_pr_do_not_inherit_an_old_continuation(board):
    tid = kb.create_task(board, title="Wait for existing work", assignee="forge")
    kb.add_comment(board, tid, "forge", "https://github.com/example/app/pull/7")
    assert kb.block_task(board, tid, reason="Wait for results", kind="transient")
    assert kb.unblock_task(board, tid, auto_resume={"trigger": "timer", "kind": "transient"})
    assert dispatch.check_respawn_guard(board, tid) == "active_pr"
    assert kb.block_task(board, tid, reason="Continue this task?", kind="needs_input")
    assert kb.unblock_task(board, tid)
    assert dispatch.check_respawn_guard(board, tid) is None
    kb.add_comment(board, tid, "forge", "Published https://github.com/example/app/pull/8")
    assert dispatch.check_respawn_guard(board, tid) == "active_pr"
    assert kb.assign_task(board, tid, "forge")
    assert dispatch.check_respawn_guard(board, tid) == "active_pr"
