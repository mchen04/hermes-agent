"""LOCAL-PATCH kanban-judge-fair: the handoff judge accepts evidence it can see and stops re-litigating.

In the week to 2026-09-27 the judge rejected 26 handoffs across 9 cards, mostly for "no new concrete evidence";
t_51171702 got 8 rejections in 11 minutes, and experiment cards whose deliverable was a comment were rejected
because the judge never saw the comment. Now the judge sees the card comments and rejects only by naming an
unmet Done-means item, a "Review: none" brief completes on its stated evidence, and after two rejections the
next handoff passes with the judge's last objection posted as a "Disputed:" comment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from hermes_cli import goals
from hermes_cli import kanban as kanban_cli
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc

EXPERIMENT = ("Objective: prove one long wait completes.\nDone means: the card holds a comment \"woke <time>\".\n"
              "Review: none (experiment).\nReturn destination: this card.")
CODING = "Done means: the fix lands with a passing test.\nReview: required."


def _board(monkeypatch, tmp_path, body):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "test-worker")
    monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="Goal card", assignee="test-worker", goal_mode=True, body=body)
        kb.claim_task(conn, tid, claimer="test-worker")
        run_id = kb.get_task(conn, tid).current_run_id
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run_id))
    return tid


class Rejecting:
    def __init__(self):
        self.calls = []

    def __call__(self, goal, last_response, **kw):
        self.calls.append({"response": last_response, **kw})
        return "continue", f"objection {len(self.calls)}: Done-means item 'passing test' has no evidence", \
            False, None, False


@pytest.fixture
def tool_judge(monkeypatch):
    from tools import kanban_tools as kt

    judge = Rejecting()
    monkeypatch.setattr(kt, "judge_goal", judge)
    monkeypatch.setattr(kt, "_goal_judge_available", lambda: True)
    return kt, judge


def _comments(tid):
    with kbc.connect_closing() as conn:
        return [(c.author, c.body) for c in kb.list_comments(conn, tid)]


def _status(tid):
    with kbc.connect_closing() as conn:
        return kb.get_task(conn, tid).status


def test_review_none_card_completes_on_its_stated_evidence(monkeypatch, tmp_path, tool_judge):
    kt, judge = tool_judge
    tid = _board(monkeypatch, tmp_path, EXPERIMENT)
    out = json.loads(kt._handle_complete({"summary": "Posted comment 926 with the wake time."}))
    assert "error" not in out
    assert judge.calls == [] and _status(tid) == "done"


def test_third_handoff_passes_after_two_rejections_with_a_disputed_comment(monkeypatch, tmp_path, tool_judge):
    kt, judge = tool_judge
    tid = _board(monkeypatch, tmp_path, CODING)
    for attempt in (1, 2):
        assert "rejected" in json.loads(kt._handle_complete({"summary": f"attempt {attempt}"}))["error"]
    assert _status(tid) == "running"
    out = json.loads(kt._handle_complete({"summary": "attempt 3"}))
    assert "error" not in out and _status(tid) == "done"
    assert len(judge.calls) == 2
    disputed = [body for author, body in _comments(tid) if body.startswith(goals.DISPUTED_PREFIX)]
    assert len(disputed) == 1 and "objection 2" in disputed[0]


def test_judge_sees_comments_and_the_retry_rule(monkeypatch, tmp_path, tool_judge):
    kt, judge = tool_judge
    tid = _board(monkeypatch, tmp_path, CODING)
    with kbc.connect_closing() as conn:
        kb.add_comment(conn, tid, "test-worker", "woke 14:02:11")
    kt._handle_complete({"summary": "first try"})
    kt._handle_complete({"summary": "second try"})
    first, second = judge.calls
    assert first["handoff"] is True and "woke 14:02:11" in first["response"] and "first try" in first["response"]
    retry = "\n".join(second["subgoals"])
    assert "objection 1" in retry and "first try" in retry and "still counts" in retry


def test_cli_gate_applies_the_same_limit(monkeypatch, tmp_path):
    import agent.auxiliary_client as aux

    tid = _board(monkeypatch, tmp_path, CODING)
    judge = Rejecting()
    monkeypatch.setattr(goals, "judge_goal", judge)
    monkeypatch.setattr(aux, "get_text_auxiliary_client", lambda *a, **k: (object(), "judge-model"))

    def complete(summary):
        return kanban_cli._cmd_complete(argparse.Namespace(
            task_ids=[tid], task_id=tid, ids=[tid], summary=summary, result=None, metadata=None, force=False))

    assert complete("one") != 0 and complete("two") != 0
    assert complete("three") == 0 and _status(tid) == "done"
    assert len(judge.calls) == 2 and all(call["handoff"] for call in judge.calls)
    assert any(body.startswith(goals.DISPUTED_PREFIX) for _, body in _comments(tid))


def test_handoff_prompt_names_the_done_means_rule(monkeypatch):
    import agent.auxiliary_client as aux

    seen = {}

    def fake_call(call_llm, system_prompt, user_prompt, timeout):
        seen["prompt"] = user_prompt
        return '{"verdict": "done", "reason": "every item has evidence"}'

    monkeypatch.setattr(aux, "call_llm", lambda *a, **k: None)
    monkeypatch.setattr(goals, "_call_goal_judge_llm", fake_call)
    verdict, *_ = goals.judge_goal("Card\n\nDone means: a comment.", "Posted comment 9.", handoff=True,
                                   subgoals=["Earlier handoffs of this card were rejected: X"])
    prompt = seen["prompt"]
    assert verdict == "done"
    assert "Done means" in prompt and "No new evidence" in prompt and "Earlier handoffs" in prompt
    assert "Additional criteria the user added" not in prompt
