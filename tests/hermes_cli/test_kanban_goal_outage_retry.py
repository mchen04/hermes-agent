"""LOCAL-PATCH kanban-judge-transient: a goal card whose coder hit a provider outage retries on the
dispatcher's transient timer instead of waiting for a person. Quota and ordinary blocks still park."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import goals
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc

# Judge reasons recorded on real goal cards (2026-09-21/22).
OUTAGE = ('Progress is blocked because ChatGPT or Codex Subscription failed on three attempts with '
          '"Non-streaming API call timed out after 1200s with no response (threshold: 1200s)"')
QUOTA = "The sole Codex writer hit its provider usage limit mid-repair; retry after the reset."


@pytest.fixture
def board(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "test-worker")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="Ship it", assignee="test-worker", goal_mode=True, body="Finish the PR.")
        kb.claim_task(conn, tid, claimer="test-worker")
        run_id = kb.get_task(conn, tid).current_run_id
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run_id))
    return tid


def _judge(monkeypatch, reason):
    monkeypatch.setattr(goals, "judge_goal", lambda *a, **k: ("blocked", reason, False, None, False))


def _age_block(tid, seconds):
    with kbc.connect_closing() as conn, kb.write_txn(conn):
        conn.execute("UPDATE task_events SET created_at = created_at - ? WHERE id = "
                     "(SELECT MAX(id) FROM task_events WHERE task_id=? AND kind='blocked')", (seconds, tid))


def test_outage_verdict_parks_the_goal_card_on_the_timer(monkeypatch, board):
    import cli

    _judge(monkeypatch, OUTAGE)
    cli._run_kanban_goal_loop_q(None, "the coder timed out", run_turn=lambda p: "x", log=lambda m: None)
    with kbc.connect_closing() as conn:
        task = kb.get_task(conn, board)
        assert (task.status, task.block_kind) == ("blocked", "transient")
        assert kb.resume_stranded_blocks(conn) == [], "not before the timer"
    _age_block(board, goals.PROVIDER_OUTAGE_RESUME_SECONDS)
    with kbc.connect_closing() as conn:
        resumed = kb.resume_stranded_blocks(conn)
        assert resumed and resumed[0]["trigger"] == "transient_timeout"
        assert kb.get_task(conn, board).status == "ready"


def test_quota_verdict_still_waits_for_a_person(monkeypatch, board):
    import cli

    _judge(monkeypatch, QUOTA)
    cli._run_kanban_goal_loop_q(None, "usage limit", run_turn=lambda p: "x", log=lambda m: None)
    _age_block(board, 7 * 3600)
    with kbc.connect_closing() as conn:
        task = kb.get_task(conn, board)
        assert task.status == "blocked" and task.block_kind != "transient"
        assert kb.resume_stranded_blocks(conn) == []


@pytest.mark.parametrize("reason, outage", [
    (OUTAGE, True),
    ("upstream Claude API 529 Overloaded after retries", True),
    ('the provider failed on all three attempts with "Request timed out."', True),
    ("HTTP 503 Service Unavailable from the model endpoint", True),
    (QUOTA, False),
    ("Codex usage limit reached (429), retry later", False),
    ("rate limit hit on the provider after a timed out after 30s retry", False),
    ("Needs Michael's GitHub credentials to push", False),
    ("500 tests still fail on the branch", False),
])
def test_only_outage_reasons_qualify(reason, outage):
    assert (goals.provider_outage_reason(reason) is not None) is outage


def test_failed_transient_park_falls_back_to_a_person(monkeypatch):
    _judge(monkeypatch, OUTAGE)
    blocked = []

    def refuse(reason, resume_after):
        raise kb.BlockRejected("lost the run")

    res = goals.run_kanban_goal_loop(
        task_id="t1", goal_text="g", run_turn=lambda p: "x", task_status_fn=lambda: "running",
        block_fn=blocked.append, first_response="r", transient_block_fn=refuse, log=lambda m: None,
    )
    assert res["outcome"] == "blocked_unachievable"
    assert blocked and blocked[0].startswith("Goal-mode judge ruled the goal unachievable")
