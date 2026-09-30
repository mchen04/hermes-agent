"""LOCAL-PATCH learn-workers: a Kanban worker's review finishes before the process exits.

Drives the real ``chat -q`` worker entry and the real one-shot finalize. Before the patch the
worker exited while its review thread was still running, so the review never completed.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

import agent.background_review_exit as bre
import cli


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(cli, "_single_query_finalize_attempted_session_ids", set())
    monkeypatch.setattr(cli, "_cleanup_done", False)
    monkeypatch.setattr(bre, "_exit_review_task", "")
    monkeypatch.delenv("HERMES_KANBAN_GOAL_MODE", raising=False)
    monkeypatch.setattr("tools.kanban_tools.register_current_worker_from_env", lambda: True)
    for name in ("_flush_one_shot_session_store", "_notify_single_query_session_finalize"):
        monkeypatch.setattr(cli, name, lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "_run_cleanup", lambda **_k: None)
    monkeypatch.setattr(cli, "_wait_for_oneshot_background_completions", lambda _c: None)
    yield
    bre._exit_review_task = ""


class _WorkerAgent:
    """An agent whose last turn ran four tool calls; its review takes 0.3 s like a short fork."""

    def __init__(self):
        self.session_id = "s_worker"
        self.valid_tool_names = {"memory", "skill_manage", "terminal"}
        self._memory_store = object()
        self._delegate_depth = 0
        self._interrupt_requested = False
        self._session_messages = [
            {"role": "user", "content": "work kanban task t_learn"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": str(i), "type": "function", "function": {"name": n, "arguments": "{}"}}
                for i, n in enumerate(("terminal", "read_file", "patch", "kanban_complete"))]},
            {"role": "assistant", "content": "done"},
        ]
        self.review_done = threading.Event()

    def _spawn_background_review(self, *, messages_snapshot, review_memory=False, review_skills=False):
        def _review():
            time.sleep(0.3)
            self.review_done.set()

        self._background_review_thread = threading.Thread(target=_review, daemon=True, name="bg-review")
        self._background_review_thread.start()


def _run_worker(monkeypatch, *, turn_result=None, review_during_turn=False):
    """Run ``hermes chat -q`` as the dispatcher would; return (exit code, agent).
    ``review_during_turn``: the last turn itself starts a review (the pre-patch finalizer did)."""
    agent = _WorkerAgent()

    def _chat(*_a, **_k):
        if review_during_turn:
            agent._spawn_background_review(messages_snapshot=agent._session_messages, review_skills=True)
        return "done"

    monkeypatch.setattr(cli, "_should_seed_interactive", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_collect_query_images", lambda q, i: (q, []))
    monkeypatch.setattr(cli, "_collect_kanban_task_images", lambda imgs: [])
    stub = SimpleNamespace(
        agent=agent,
        session_id="s_worker",
        _single_query_mode=False,
        _claim_active_session=lambda *a, **k: True,
        _release_active_session=lambda: None,
        console=SimpleNamespace(print=lambda *a, **k: None),
        _show_security_advisories=lambda: None,
        chat=_chat,
        _print_exit_summary=lambda **k: None,
        _last_turn_result=turn_result or {"final_response": "done", "completed": True},
    )
    with pytest.raises(SystemExit) as exc:
        cli._run_single_query_mode(stub, "work kanban task t_learn", None, False, True)
    return exc.value.code, agent


def test_kanban_worker_exit_waits_for_its_review(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_learn")
    code, agent = _run_worker(monkeypatch)
    assert code == 0
    assert agent.review_done.is_set(), "the worker exited before its review finished"


def test_review_started_by_the_last_turn_finishes_before_exit(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_learn")
    code, agent = _run_worker(monkeypatch, review_during_turn=True)
    assert code == 0
    assert agent.review_done.is_set(), "the worker exited before its review finished"


def test_failed_worker_is_reviewed_too(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_learn")
    code, agent = _run_worker(monkeypatch, turn_result={"failed": True})
    assert code == 1
    assert agent.review_done.is_set()


def test_interrupted_worker_skips_the_review(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_learn")
    code, agent = _run_worker(monkeypatch, turn_result={"completed": False, "interrupted": True})
    assert code == 130
    assert not hasattr(agent, "_background_review_thread")


def test_plain_one_shot_does_not_wait(monkeypatch):
    """Scripts use ``chat -q`` too: outside a Kanban worker nothing is deferred or awaited."""
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    code, agent = _run_worker(monkeypatch)
    assert code == 0
    assert not hasattr(agent, "_background_review_thread")


def test_worker_with_the_wait_disabled_keeps_the_old_exit(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_learn")
    monkeypatch.setattr(bre, "worker_review_wait_seconds", lambda: 0.0)
    code, agent = _run_worker(monkeypatch)
    assert code == 0
    assert not hasattr(agent, "_background_review_thread")
