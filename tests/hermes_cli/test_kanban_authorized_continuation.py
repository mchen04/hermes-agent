"""A new continuation dispatches the same task once; a timer is not permission."""
from pathlib import Path
from contextlib import contextmanager
import threading
import time
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


def test_new_pr_comment_after_answered_unblock_keeps_guard(board, monkeypatch):
    tid = kb.create_task(board, title="Continue PR", assignee="forge")
    kb.add_comment(board, tid, "forge", "https://github.com/example/app/pull/7")
    assert kb.block_task(board, tid, reason="Continue?", kind="needs_input")
    writer_ready = threading.Event()
    start_comment = threading.Event()
    comment_waiting = threading.Event()
    errors = []
    real_write_txn = kb.write_txn
    real_append = kb._append_event

    @contextmanager
    def write_txn(conn, *args, **kwargs):
        if threading.current_thread() is writer:
            comment_waiting.set()
        with real_write_txn(conn, *args, **kwargs):
            yield

    def append_event(conn, task_id, kind, *args, **kwargs):
        if kind == "auto_resumed":
            start_comment.set()
            assert comment_waiting.wait(5)
            time.sleep(2.1)
        return real_append(conn, task_id, kind, *args, **kwargs)

    def post_new_pr():
        try:
            with connect() as rival:
                writer_ready.set()
                assert start_comment.wait(5)
                kb.add_comment(rival, tid, "forge", "https://github.com/example/app/pull/8")
        except BaseException as exc:
            errors.append(exc)

    writer = threading.Thread(target=post_new_pr)
    monkeypatch.setattr(kb, "write_txn", write_txn)
    monkeypatch.setattr(kb, "_append_event", append_event)
    writer.start()
    assert writer_ready.wait(5)
    assert kb.unblock_task(board, tid, auto_resume={"trigger": "answered", "kind": "needs_input"})
    writer.join(5)
    assert not writer.is_alive()
    assert not errors
    events = [dict(row) for row in board.execute("SELECT kind,payload,created_at FROM task_events WHERE task_id=? AND kind IN ('unblocked','auto_resumed') ORDER BY id", (tid,))]
    comments = [dict(row) for row in board.execute("SELECT id,body,created_at FROM task_comments WHERE task_id=? ORDER BY id", (tid,))]
    assert dispatch.check_respawn_guard(board, tid) == "active_pr", (events, comments)
