"""LOCAL-PATCH kanban-judge-memory: a goal-mode handoff judged after a rejection sees that rejection.

2026-09-22 22:14:11 the judge rejected Kestrel card t_04dc2922's completion (missing latency, optimization and
interruption evidence); 34 s later a reworded summary with no new evidence was accepted, because each gate call
judged the summary alone. The gate now records each rejection and hands the latest one (reason and the rejected
summary) to the next judge call as a criterion that only new evidence can satisfy. An amended brief clears it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from hermes_cli import goals
from hermes_cli import kanban as kanban_cli
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc

REJECTION = "No measured interruption latency, no optimization comparison and no barge-in evidence."
FIRST = "Selected MiniCPM-o 4.5 Q4; demos runnable; findings in findings.md."
REWORDED = "Completed the research: chose repaired MiniCPM-o 4.5 Q4 as best tradeoff, runnable offline demos."


@pytest.fixture
def goal_task(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "test-worker")
    monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="Kestrel speech model", assignee="test-worker", goal_mode=True,
                             body="Measure latency, optimize, and prove interruption handling.")
        kb.claim_task(conn, tid, claimer="test-worker")
        run_id = kb.get_task(conn, tid).current_run_id
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run_id))
    return tid, run_id


class Judge:
    """Rejects the first handoff, then records what the next call was shown and approves."""

    def __init__(self):
        self.calls = []

    def __call__(self, goal, last_response, **kw):
        self.calls.append({"response": last_response, **kw})
        if len(self.calls) == 1:
            return "continue", REJECTION, False, None, False
        return "done", "ok", False, None, False


def _criteria(call):
    return "\n".join(call.get("subgoals") or [])


def test_tool_gate_shows_the_previous_rejection_to_the_next_judge(monkeypatch, goal_task):
    from tools import kanban_tools as kt

    tid, _ = goal_task
    judge = Judge()
    monkeypatch.setattr(kt, "judge_goal", judge)
    monkeypatch.setattr(kt, "_goal_judge_available", lambda: True)
    out = json.loads(kt._handle_complete({"summary": FIRST}))
    assert "rejected" in out["error"]
    assert not judge.calls[0].get("subgoals")
    json.loads(kt._handle_complete({"summary": REWORDED}))
    shown = _criteria(judge.calls[1])
    assert REJECTION in shown and FIRST in shown
    assert "reworded" in shown and "new" in shown.lower()


def test_amended_brief_clears_the_previous_rejection(monkeypatch, goal_task):
    from tools import kanban_tools as kt

    tid, _ = goal_task
    judge = Judge()
    monkeypatch.setattr(kt, "judge_goal", judge)
    monkeypatch.setattr(kt, "_goal_judge_available", lambda: True)
    json.loads(kt._handle_complete({"summary": FIRST}))
    with kbc.connect_closing() as conn:
        assert kb.amend_task_body(conn, tid, body="Pick the best turn-based model; interruption is out of scope.",
                                  author="michael", reason="scope cut")
    json.loads(kt._handle_complete({"summary": REWORDED}))
    assert not judge.calls[1].get("subgoals")


def test_cli_gate_shows_the_previous_rejection_to_the_next_judge(monkeypatch, goal_task, capsys):
    import agent.auxiliary_client as aux

    tid, _ = goal_task
    judge = Judge()
    monkeypatch.setattr(goals, "judge_goal", judge)
    monkeypatch.setattr(aux, "get_text_auxiliary_client", lambda *a, **k: (object(), "judge-model"))

    def complete(summary):
        return kanban_cli._cmd_complete(argparse.Namespace(
            task_ids=[tid], task_id=tid, ids=[tid], summary=summary, result=None, metadata=None, force=False))

    assert complete(FIRST) != 0
    complete(REWORDED)
    shown = _criteria(judge.calls[1])
    assert REJECTION in shown and FIRST in shown
