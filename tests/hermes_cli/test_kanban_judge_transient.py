"""LOCAL-PATCH kanban-judge-transient: a judge BLOCKED verdict caused by a provider failure parks the card
on a timer (transient, auto-retry) instead of ruling the goal unachievable."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli import goals
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc

TIMEOUT_REASON = (
    'Progress is blocked because ChatGPT or Codex Subscription failed on three attempts with '
    '"Non-streaming API call timed out after 1200s (provider=codex)".'
)
OVERLOADED_REASON = "The worker reported upstream Claude API 529 Overloaded and could not continue."


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
        tid = kb.create_task(conn, title="Ship the report", assignee="test-worker", goal_mode=True,
                             body="Publish the report and close.")
        kb.claim_task(conn, tid, claimer="test-worker")
        run_id = kb.get_task(conn, tid).current_run_id
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run_id))
    return tid, run_id


def _scripted_judge(monkeypatch, verdict, reason):
    monkeypatch.setattr(goals, "judge_goal", lambda *a, **k: (verdict, reason, False, None, False))


@pytest.mark.parametrize("text", [
    TIMEOUT_REASON, OVERLOADED_REASON, "API call failed after 3 retries", "Request timed out",
    "google: RESOURCE_EXHAUSTED", "hit the rate limit", "failed on all three attempts",
])
def test_provider_failure_signatures_match(text):
    assert goals.provider_failure_reason(text) == goals.PROVIDER_FAILURE_PREFIX + text


def test_genuine_unachievable_reason_does_not_match():
    assert goals.provider_failure_reason("The dataset the card names does not exist anywhere.") is None


def test_loop_parks_provider_failure_with_timer_not_needs_input(monkeypatch):
    _scripted_judge(monkeypatch, "blocked", TIMEOUT_REASON)
    parked = []
    res = goals.run_kanban_goal_loop(
        task_id="t1", goal_text="ship it", run_turn=lambda p: pytest.fail("must not re-poke"),
        task_status_fn=lambda: "running",
        block_fn=lambda r: pytest.fail(f"must not file an unachievable block: {r}"),
        transient_block_fn=lambda reason, resume_after: parked.append((reason, resume_after)),
        first_response="Codex failed three times.",
    )
    assert res["outcome"] == "blocked_transient"
    assert parked == [(goals.PROVIDER_FAILURE_PREFIX + TIMEOUT_REASON, 600)]


def test_loop_matches_the_judged_response_too(monkeypatch):
    _scripted_judge(monkeypatch, "blocked", "Progress is blocked; the coder cannot continue.")
    parked = []
    res = goals.run_kanban_goal_loop(
        task_id="t1", goal_text="ship it", run_turn=lambda p: "", task_status_fn=lambda: "running",
        block_fn=lambda r: pytest.fail("must not file an unachievable block"),
        transient_block_fn=lambda reason, resume_after: parked.append(resume_after),
        first_response="delegate error: upstream Claude API 529 Overloaded",
    )
    assert res["outcome"] == "blocked_transient" and parked == [600]


def test_loop_still_blocks_a_genuinely_unachievable_goal(monkeypatch):
    _scripted_judge(monkeypatch, "blocked", "The deliverable cannot exist: the source repo was deleted.")
    blocked = []
    res = goals.run_kanban_goal_loop(
        task_id="t1", goal_text="ship it", run_turn=lambda p: "", task_status_fn=lambda: "running",
        block_fn=blocked.append, transient_block_fn=lambda *a: pytest.fail("not transient"),
        first_response="I looked everywhere.",
    )
    assert res["outcome"] == "blocked_unachievable"
    assert blocked and blocked[0].startswith("Goal-mode judge ruled the goal unachievable")


def test_worker_loop_lands_transient_block_that_the_dispatcher_resumes(monkeypatch, goal_task):
    import cli

    tid, run_id = goal_task
    _scripted_judge(monkeypatch, "blocked", TIMEOUT_REASON)
    cli._run_kanban_goal_loop_q(None, "Codex timed out three times.", run_turn=lambda p: "", log=lambda m: None)
    with kbc.connect_closing() as conn:
        task = kb.get_task(conn, tid)
        assert (task.status, task.block_kind) == ("blocked", "transient")
        event = [e for e in kb.list_events(conn, tid) if e.kind == "blocked"][-1]
        payload = event.payload
        assert payload["resume_after"] == 600 and payload["auto_resumable"] is True
        assert payload["reason"].startswith("Provider failure (auto-retry): ")
        assert "timed out after 1200s" in payload["reason"]
        resumed = kb.resume_stranded_blocks(conn, now=event.created_at + 601)
        assert resumed and resumed[0]["trigger"] == "transient_timeout"
        assert kb.get_task(conn, tid).status != "blocked"


def test_complete_gate_parks_provider_failure_instead_of_rejecting_forever(monkeypatch, goal_task):
    from tools import kanban_tools as kt

    tid, run_id = goal_task
    monkeypatch.setattr(kt, "judge_goal", lambda *a, **k: ("blocked", OVERLOADED_REASON, False, None, False))
    monkeypatch.setattr(kt, "_goal_judge_available", lambda: True)
    out = json.loads(kt._handle_complete({"summary": "Claude returned 529 Overloaded on every attempt."}))
    assert "error" in out and "transient block" in out["error"] and "unachievable" not in out["error"]
    with kbc.connect_closing() as conn:
        task = kb.get_task(conn, tid)
        assert (task.status, task.block_kind) == ("blocked", "transient")
