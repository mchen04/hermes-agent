import hashlib
import json
import shlex
from pathlib import Path

import pytest

from hermes_cli import kanban as kc
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc

AUTH_TEXT = "Michael (verbatim): yes"
REASON = "Michael approved ordinary event-aware waiting and stale-capacity clearance"


@pytest.fixture
def board(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "forge")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for key in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID",
                "HERMES_DELEGATED_CHILD_CONTEXT", "HERMES_SESSION_ID"):
        monkeypatch.delenv(key, raising=False)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="Hill-climb", body="Keep improving.", assignee="forge", goal_mode=True)
        assert kb.claim_task(conn, tid) is not None
        assert kb.block_task(conn, tid, reason="waiting for compute", kind="capacity")
        assert kb.unblock_task(conn, tid)
        assert kb.claim_task(conn, tid) is not None
        auth = kb.add_comment(conn, tid, author="default", body=AUTH_TEXT)
    return {"task": tid, "auth": auth}


def change(conn, board, **kwargs):
    from hermes_cli.kanban_lifecycle_edit import change_lifecycle
    kwargs.setdefault("goal_mode", False)
    kwargs.setdefault("clear_stale_block", True)
    kwargs.setdefault("reason", REASON)
    kwargs.setdefault("authorization_comment_id", board["auth"])
    return change_lifecycle(conn, board["task"], **kwargs)


def lifecycle(conn, task_id):
    task = kb.get_task(conn, task_id)
    return task.status, task.goal_mode, task.block_kind, task.block_recurrences


def lifecycle_edits(conn, task_id):
    rows = conn.execute("SELECT payload FROM task_events WHERE task_id=? AND kind='edited'", (task_id,))
    return [json.loads(r[0]) for r in rows]


def as_worker(monkeypatch, task_id):
    with kbc.connect() as conn:
        run_id = kb._current_run_id(conn, task_id)
    monkeypatch.setenv("HERMES_KANBAN_TASK", task_id)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run_id))


def test_operator_cli_ends_goal_mode_and_clears_a_stale_capacity_block_so_the_worker_can_wait(board, monkeypatch):
    from tools import kanban_tools as kt
    tid = board["task"]
    with kbc.connect() as conn:
        assert lifecycle(conn, tid) == ("running", True, "capacity", 1)
        assert kb.task_waits_on(kb.get_task(conn, tid)) is None
        (blocked,) = conn.execute("SELECT id FROM task_events WHERE task_id=? AND kind='blocked'", (tid,))
    as_worker(monkeypatch, tid)
    assert "goal_mode" in json.loads(kt._handle_schedule({"reason": "SCHEDULED_UNTIL=2026-11-01T03:00:00-07:00"}))["error"]
    monkeypatch.delenv("HERMES_KANBAN_TASK")

    out = kc.run_slash(f"edit {tid} --goal-mode off --clear-stale-block "
                       f"--authorization-comment {board['auth']} --reason {shlex.quote(REASON)}")
    assert out == f"Edited {tid}: goal_mode, block_kind, block_recurrences"
    shown = json.loads(kc.run_slash(f"show {tid} --json"))["task"]
    assert (shown["status"], shown["goal_mode"], shown["block_kind"], shown["block_recurrences"]) == (
        "running", False, None, 0)
    with kbc.connect() as conn:
        (event,) = lifecycle_edits(conn, tid)
        assert event["fields"] == ["goal_mode", "block_kind", "block_recurrences"]
        assert event["goal_mode"] == {"from": True, "to": False}
        assert event["cleared_block"] == {"kind": "capacity", "recurrences": 1, "blocked_event_id": blocked[0]}
        assert (event["status"], event["reason"], event["origin"]) == ("running", REASON, "operator")
        assert event["authorization"]["id"] == board["auth"]
        assert event["authorization"]["sha256"] == hashlib.sha256(AUTH_TEXT.encode()).hexdigest()
        assert conn.execute("SELECT COUNT(*) FROM task_events WHERE task_id=? AND kind='blocked'",
                            (tid,)).fetchone()[0] == 1

    as_worker(monkeypatch, tid)
    scheduled = json.loads(kt._handle_schedule({"reason": "SCHEDULED_UNTIL=2026-11-01T03:00:00-07:00"}))
    assert scheduled.get("error") is None
    with kbc.connect() as conn:
        assert lifecycle(conn, tid) == ("scheduled", False, None, 0)


@pytest.mark.parametrize("held,status", [("needs_input", "blocked"), ("capacity", "triage"), ("dependency", "todo")])
def test_a_block_the_card_still_waits_on_is_refused_and_nothing_changes(board, held, status):
    with kbc.connect() as conn:
        if held == "dependency":
            board["task"] = kb.create_task(conn, title="Child", assignee="forge", goal_mode=True)
            kb.link_tasks(conn, parent_id=kb.create_task(conn, title="Open parent"), child_id=board["task"])
            with kb.write_txn(conn):
                conn.execute("UPDATE tasks SET status='running' WHERE id=?", (board["task"],))
            board["auth"] = kb.add_comment(conn, board["task"], author="default", body=AUTH_TEXT)
        assert kb.block_task(conn, board["task"], reason="wait", kind=held)
        before = lifecycle(conn, board["task"])
        assert before[:3] == (status, True, held)
        with pytest.raises(ValueError, match="still waits on"):
            change(conn, board)
        assert lifecycle(conn, board["task"]) == before
        assert lifecycle_edits(conn, board["task"]) == []


@pytest.mark.parametrize("caller", ["own_worker", "other_card_worker", "delegated_child"])
def test_workers_cannot_change_any_cards_lifecycle(board, monkeypatch, caller):
    with kbc.connect() as conn:
        other = kb.create_task(conn, title="Other card", assignee="forge")
        env = {"own_worker": ("HERMES_KANBAN_TASK", board["task"]),
               "other_card_worker": ("HERMES_KANBAN_TASK", other),
               "delegated_child": ("HERMES_DELEGATED_CHILD_CONTEXT", "1")}[caller]
        monkeypatch.setenv(*env)
        with pytest.raises(PermissionError):
            change(conn, board)
        if caller == "own_worker":
            out = kc.run_slash(f"edit {board['task']} --goal-mode off --authorization-comment {board['auth']} "
                               f"--reason escape")
            assert "Kanban workers cannot change" in out
        monkeypatch.delenv(env[0])
        assert lifecycle(conn, board["task"]) == ("running", True, "capacity", 1)
        assert lifecycle_edits(conn, board["task"]) == []


def _comment_on_another_card(conn, board):
    other = kb.create_task(conn, title="Other card")
    return {"authorization_comment_id": kb.add_comment(conn, other, author="default", body=AUTH_TEXT)}


@pytest.mark.parametrize("fault,error", [
    (lambda conn, board: {"authorization_comment_id": board["auth"] + 100}, PermissionError),
    (_comment_on_another_card, PermissionError),
    (lambda conn, board: {"reason": " "}, ValueError),
    (lambda conn, board: {"goal_mode": None, "clear_stale_block": False}, ValueError),
], ids=["missing_comment", "cross_card", "no_reason", "no_change_asked"])
def test_authorization_and_reason_are_required(board, fault, error):
    with kbc.connect() as conn:
        kwargs = fault(conn, board)
        with pytest.raises(error):
            change(conn, board, **kwargs)
        assert lifecycle(conn, board["task"]) == ("running", True, "capacity", 1)
        assert lifecycle_edits(conn, board["task"]) == []


def test_an_edit_already_in_place_records_nothing_and_goal_mode_can_be_restored(board):
    tid = board["task"]
    with kbc.connect() as conn:
        assert change(conn, board) == ["goal_mode", "block_kind", "block_recurrences"]
        assert change(conn, board) == []
        assert len(lifecycle_edits(conn, tid)) == 1
    out = kc.run_slash(f"edit {tid} --goal-mode off --clear-stale-block "
                       f"--authorization-comment {board['auth']} --reason again")
    assert out == f"Unchanged {tid}: goal mode and block record already as requested"
    with kbc.connect() as conn:
        assert change(conn, board, goal_mode=True, clear_stale_block=False) == ["goal_mode"]
        assert lifecycle(conn, tid) == ("running", True, None, 0)
        assert lifecycle_edits(conn, tid)[-1]["goal_mode"] == {"from": False, "to": True}
