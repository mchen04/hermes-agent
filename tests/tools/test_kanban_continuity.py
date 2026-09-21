"""Ordinary supervisor waits retain ownership; incremental reads retain new decisions."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def board(monkeypatch, tmp_path):
    from hermes_cli import kanban_db as kb, kanban_db_connect as kbc
    import tools.kanban_tools  # register production handlers

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="Supervise coding", assignee="worker", goal_mode=True,
                             body="Finish the approved repair and retain its acceptance criteria.")
        kb.claim_task(conn, tid, claimer="worker")
        task = kb.get_task(conn, tid)
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(task.current_run_id))
    return kb, kbc, tid


def test_normal_goal_wait_cannot_end_owner_or_create_another_run(board):
    from tools.registry import registry

    kb, kbc, tid = board
    with kbc.connect_closing() as conn:
        original = kb.get_task(conn, tid)
    result = json.loads(registry.dispatch("kanban_block", {
        "kind": "transient", "reason": "The coding worker is still running.", "resume_after": 600,
    }))
    assert "error" in result
    assert "same session" in result["error"]
    with kbc.connect_closing() as conn:
        with pytest.raises(kb.BlockRejected, match="same session"):
            kb.block_task(conn, tid, kind="transient", reason="Worker running",
                          expected_run_id=original.current_run_id)
        assert kb.resume_stranded_blocks(conn, now=2_000_000_000) == []
        current = kb.get_task(conn, tid)
        assert current.status == "running"
        assert (current.current_run_id, current.claim_lock) == (original.current_run_id, original.claim_lock)
        assert len(kb.list_runs(conn, tid)) == 1
        assert kb.claim_task(conn, tid, claimer="duplicate") is None
        assert kb.block_task(conn, tid, kind="needs_input", reason="Need a user decision",
                             expected_run_id=original.current_run_id)
        assert kb.get_task(conn, tid).block_kind == "needs_input"


def test_task_cursor_keeps_same_second_decisions_without_repeating_brief(board):
    from tools.registry import registry

    kb, kbc, tid = board
    with kbc.connect_closing() as conn:
        kb.add_comment(conn, tid, "user", "Old approval: implement all repairs.")
    first = json.loads(registry.dispatch("kanban_show", {}))
    assert first["task"]["body"]
    with kbc.connect_closing() as conn:
        kb.add_comment(conn, tid, "user", "New decision: preserve the existing coding owner.")
    second = json.loads(registry.dispatch("kanban_show", {"cursor": first.get("cursor")}))
    assert "body" not in second["task"]
    assert "worker_context" not in second
    assert [c["body"] for c in second["comments"]] == ["New decision: preserve the existing coding owner."]
    third = json.loads(registry.dispatch("kanban_show", {"cursor": second["cursor"]}))
    assert third["comments"] == [] and third["events"] == []
    assert third["task"]["current_run_id"] == first["task"]["current_run_id"]


def test_goal_dependency_and_explicit_operator_park_are_preserved(board):
    kb, kbc, tid = board
    with kbc.connect_closing() as conn:
        task = kb.get_task(conn, tid)
        parent = kb.create_task(conn, title="Required upstream result", assignee="worker", goal_mode=True)
        kb.link_tasks(conn, parent, tid, expected_child_run_id=task.current_run_id)
        assert kb.block_task(conn, tid, kind="dependency", reason="Need upstream result",
                             expected_run_id=task.current_run_id)
        assert kb.get_task(conn, tid).status == "todo"
        assert kb.recompute_ready(conn) == 0
        assert kb.block_task(conn, parent, kind="transient", reason="Operator requested retry", force=True)
        assert kb.get_task(conn, parent).status == "blocked"


def test_cursor_returns_a_revised_brief_in_full(board):
    from tools.registry import registry

    kb, kbc, tid = board
    first = json.loads(registry.dispatch("kanban_show", {}))
    with kbc.connect_closing() as conn:
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='triage' WHERE id=?", (tid,))
        assert kb.specify_triage_task(conn, tid, body="Revised acceptance criteria.", author="user")
    revised = json.loads(registry.dispatch("kanban_show", {"cursor": first["cursor"]}))
    assert revised["task"]["body"] == "Revised acceptance criteria."


def test_cursor_pages_without_skipping_or_accepting_another_task(board):
    from tools.registry import registry

    kb, kbc, tid = board
    first = json.loads(registry.dispatch("kanban_show", {}))
    with kbc.connect_closing() as conn:
        for index in range(101):
            kb.add_comment(conn, tid, "user", f"Decision {index}")
    page = json.loads(registry.dispatch("kanban_show", {"cursor": first["cursor"]}))
    assert len(page["comments"]) == 100 and page["truncated"]["comments"]
    assert len(page["events"]) == 100 and page["truncated"]["events"]
    last = json.loads(registry.dispatch("kanban_show", {"cursor": page["cursor"]}))
    assert [c["body"] for c in last["comments"]] == ["Decision 100"]
    assert not any(last["truncated"].values())
    assert {c["id"] for c in page["comments"]}.isdisjoint(c["id"] for c in last["comments"])
    wrong_task = {**last["cursor"], "task_id": "another-task"}
    assert "error" in json.loads(registry.dispatch("kanban_show", {"cursor": wrong_task}))
    wrong_board = {**last["cursor"], "database": "another-board"}
    assert "error" in json.loads(registry.dispatch("kanban_show", {"cursor": wrong_board}))


def test_concurrent_comment_is_delivered_on_the_next_snapshot(board, monkeypatch):
    from tools.registry import registry

    kb, kbc, tid = board
    original = kb.list_comments

    def comment_during_read(conn, task_id):
        monkeypatch.setattr(kb, "list_comments", original)
        with kbc.connect_closing() as writer:
            kb.add_comment(writer, tid, "user", "Decision arriving during a read.")
        return original(conn, task_id)

    monkeypatch.setattr(kb, "list_comments", comment_during_read)
    first = json.loads(registry.dispatch("kanban_show", {}))
    assert first["comments"] == []
    monkeypatch.setattr(kb, "list_comments", original)
    second = json.loads(registry.dispatch("kanban_show", {"cursor": first["cursor"]}))
    assert [c["body"] for c in second["comments"]] == ["Decision arriving during a read."]
