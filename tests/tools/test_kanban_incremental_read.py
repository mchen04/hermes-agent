"""LOCAL-PATCH kanban-incremental-read + kanban-compact-read: kanban_show returns a cursor; later reads
return only new decisions, and a cursor-less read keeps the latest comments and runs."""

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
        tid = kb.create_task(conn, title="Supervise coding", assignee="worker",
                             body="Finish the approved repair and retain its acceptance criteria.")
        kb.claim_task(conn, tid, claimer="worker")
        task = kb.get_task(conn, tid)
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(task.current_run_id))
    return kb, kbc, tid




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


def test_orientation_read_keeps_only_the_latest_comments_and_runs(board):
    """A cursor-less read is bounded; cursor reads are unchanged."""
    from tools.registry import registry

    kb, kbc, tid = board
    with kbc.connect_closing() as conn:
        for i in range(40):
            kb.add_comment(conn, tid, "user", f"note {i}")
        with kb.write_txn(conn):
            for i in range(11):  # 1 live run + 11 closed ones
                conn.execute("INSERT INTO task_runs (task_id, status, started_at, ended_at) VALUES (?, ?, ?, ?)",
                             (tid, "crashed", 1_000 + i, 1_001 + i))
    first = json.loads(registry.dispatch("kanban_show", {}))
    assert len(first["comments"]) == 30
    assert [c["body"] for c in first["comments"]][0] == "note 10"
    assert first["comments"][-1]["body"] == "note 39"
    assert len(first["runs"]) == 10 and first["runs"][0]["id"] == 4 and first["runs"][-1]["id"] == 1  # live run last
    assert first["truncated"] == {"events": False, "comments": True, "runs": True}
    assert first["note"].startswith("older comments/runs omitted")
    assert first["task"]["body"] and first["worker_context"]
    with kbc.connect_closing() as conn:
        kb.add_comment(conn, tid, "user", "note 40")
    second = json.loads(registry.dispatch("kanban_show", {"cursor": first["cursor"]}))
    assert [c["body"] for c in second["comments"]] == ["note 40"]
    assert second["truncated"] == {"events": False, "comments": False}
    assert "note" not in second and "worker_context" not in second


def test_orientation_read_without_overflow_has_no_note(board):
    from tools.registry import registry

    kb, kbc, tid = board
    with kbc.connect_closing() as conn:
        kb.add_comment(conn, tid, "user", "one note")
    first = json.loads(registry.dispatch("kanban_show", {}))
    assert len(first["comments"]) == 1
    assert first["truncated"] == {"events": False, "comments": False, "runs": False}
    assert "note" not in first
