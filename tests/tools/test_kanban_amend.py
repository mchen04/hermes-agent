"""LOCAL-PATCH kanban-amend: a worker (or the CLI) replaces an open card's brief when the objective changes,
and the goal-mode judge is then evaluated against the new brief."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from hermes_cli import goals
from hermes_cli import kanban as kanban_cli
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


@pytest.fixture
def board(monkeypatch, tmp_path):
    import tools.kanban_tools  # register production handlers

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "test-worker")
    monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="Ship the report", assignee="test-worker", goal_mode=True,
                             body="Run all six experiments, then publish.")
        kb.claim_task(conn, tid, claimer="test-worker")
        run_id = kb.get_task(conn, tid).current_run_id
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run_id))
    return tid, run_id


def test_worker_amend_replaces_body_and_records_event_and_comment(board):
    from tools.registry import registry

    tid, run_id = board
    first = json.loads(registry.dispatch("kanban_show", {}))
    out = json.loads(registry.dispatch("kanban_amend", {
        "body": "Stop the experiments. Publish what exists and close.",
        "reason": "Michael: stop the experiments, publish, close.",
    }))
    assert out.get("task_id") == tid and "error" not in out
    shown = json.loads(registry.dispatch("kanban_show", {}))
    assert shown["task"]["body"] == "Stop the experiments. Publish what exists and close."
    edited = [e for e in shown["events"] if e["kind"] == "edited"]
    assert len(edited) == 1
    assert edited[0]["payload"]["reason"] == "Michael: stop the experiments, publish, close."
    assert edited[0]["run_id"] == run_id
    assert shown["comments"][-1]["body"] == "AMENDED: Michael: stop the experiments, publish, close."
    assert shown["comments"][-1]["author"] == "test-worker"
    # An incremental read after an edit re-sends the full task so the new brief reaches the worker.
    delta = json.loads(registry.dispatch("kanban_show", {"cursor": first["cursor"]}))
    assert delta["task"]["body"] == "Stop the experiments. Publish what exists and close."


def test_worker_amend_requires_reason_and_own_task(board):
    from tools.registry import registry

    tid, _ = board
    out = json.loads(registry.dispatch("kanban_amend", {"body": "new brief"}))
    assert "reason is required" in out["error"]
    with kbc.connect_closing() as conn:
        other = kb.create_task(conn, title="someone else's card", assignee="test-worker", body="untouched")
    out = json.loads(registry.dispatch("kanban_amend", {"task_id": other, "body": "hijack", "reason": "x"}))
    assert "refusing to mutate" in out["error"]
    with kbc.connect_closing() as conn:
        assert kb.get_task(conn, other).body == "untouched"


def test_amend_refuses_done_task(board):
    tid, run_id = board
    with kbc.connect_closing() as conn:
        assert kb.complete_task(conn, tid, summary="done", expected_run_id=run_id)
        assert kb.amend_task_body(conn, tid, body="too late", author="michael", reason="late change") is False
        assert kb.get_task(conn, tid).body == "Run all six experiments, then publish."


def _edit_args(**kw):
    base = dict(task_id=None, body=None, result=None, reason=None, summary=None, metadata=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_cli_edit_body_amends_a_running_task(board, capsys):
    tid, _ = board
    rc = kanban_cli._cmd_edit(_edit_args(task_id=tid, body="Publish and close.", reason="objective changed"))
    assert rc == 0 and f"Amended {tid}" in capsys.readouterr().out
    with kbc.connect_closing() as conn:
        task = kb.get_task(conn, tid)
        assert task.body == "Publish and close." and task.status == "running"
        assert kb.list_comments(conn, tid)[-1].body == "AMENDED: objective changed"
        assert [e.kind for e in kb.list_events(conn, tid)][-1] == "edited"


def test_cli_edit_body_unknown_id_fails_cleanly(board, capsys):
    rc = kanban_cli._cmd_edit(_edit_args(task_id="t_nope", body="x", reason="y"))
    assert rc == 1 and "cannot amend t_nope" in capsys.readouterr().err
    rc = kanban_cli._cmd_edit(_edit_args(task_id="t_nope"))
    assert rc == 2 and "provide --title, --body, --priority, or --result" in capsys.readouterr().err


def test_cli_edit_result_still_backfills_a_done_task(board, capsys):
    tid, run_id = board
    with kbc.connect_closing() as conn:
        assert kb.complete_task(conn, tid, summary="done", expected_run_id=run_id)
    assert kanban_cli._cmd_edit(_edit_args(task_id=tid, result="final result")) == 0
    with kbc.connect_closing() as conn:
        assert kb.get_task(conn, tid).result == "final result"


def test_goal_loop_judges_the_amended_brief_between_turns(monkeypatch, board):
    import cli

    tid, run_id = board
    judged = []

    def fake_judge(goal, response, **_kw):
        judged.append(goal)
        return "continue", "not yet", False, None, False

    monkeypatch.setattr(goals, "judge_goal", fake_judge)
    turns = []

    def run_turn(prompt):
        turns.append(prompt)
        with kbc.connect_closing() as conn:
            if len(turns) == 1:
                assert kb.amend_task_body(conn, tid, body="Publish and close.", author="test-worker",
                                          reason="Michael changed the objective")
            else:
                assert kb.complete_task(conn, tid, summary="published", expected_run_id=run_id)
        return "worked"

    cli._run_kanban_goal_loop_q(None, "first answer", run_turn=run_turn, log=lambda m: None)
    assert judged == ["Ship the report\n\nRun all six experiments, then publish.",
                      "Ship the report\n\nPublish and close."]
    with kbc.connect_closing() as conn:
        assert kb.get_task(conn, tid).status == "done"


# LOCAL-PATCH kanban-amend-noop (2026-09-23): on 2026-09-22 07:52 and 08:36 two amends wrote a body identical to
# the current brief; the worker then saw an "amendment notice" with nothing changed. An identical body is refused.
def test_amend_refuses_identical_body(board):
    tid, _ = board
    with kbc.connect_closing() as conn:
        before = len(kb.list_events(conn, tid)), len(kb.list_comments(conn, tid))
        with pytest.raises(ValueError, match="identical to the current brief"):
            kb.amend_task_body(conn, tid, body="  Run all six experiments, then publish.\n", author="michael",
                               reason="restate")
        assert (len(kb.list_events(conn, tid)), len(kb.list_comments(conn, tid))) == before


def test_worker_amend_identical_body_is_a_clear_error(board):
    from tools.registry import registry

    out = json.loads(registry.dispatch("kanban_amend", {
        "body": "Run all six experiments, then publish.", "reason": "restate"}))
    assert "identical to the current brief" in out["error"]


def test_cli_edit_identical_body_is_a_clear_error(board, capsys):
    tid, _ = board
    rc = kanban_cli._cmd_edit(_edit_args(task_id=tid, body="Run all six experiments, then publish.", reason="x"))
    assert rc == 1 and "identical to the current brief" in capsys.readouterr().err
    with kbc.connect_closing() as conn:
        assert [e.kind for e in kb.list_events(conn, tid)][-1] != "edited"
