"""LOCAL-PATCH learn-workers: Kanban workers review their session at exit and wait for it.

A worker process exits right after its last turn, so the post-turn review thread died with it
(forge: 68 review starts, 0 completions). The worker now defers the review to exit, runs one
review over the whole session, and joins it with a bounded wait.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

import agent.background_review_exit as bre
from tests.agent.test_skip_background_review import _make_agent, _run_finalize, _stub_agent_for_finalize


@pytest.fixture
def kanban_worker(monkeypatch):
    """This process is the dispatcher-owned worker for t_learn and promised an exit review."""
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_learn")
    monkeypatch.setattr(bre, "_exit_review_task", "")
    bre.defer_reviews_to_exit()
    yield
    bre._exit_review_task = ""


def _call(name: str) -> dict:
    return {"id": name, "type": "function", "function": {"name": name, "arguments": "{}"}}


def _session(*tool_names: str) -> list:
    return [
        {"role": "user", "content": "work kanban task t_learn"},
        {"role": "assistant", "content": "", "tool_calls": [_call(n) for n in tool_names]},
        {"role": "assistant", "content": "done"},
    ]


class _ReviewingAgent:
    """Stands in for AIAgent: ``_spawn_background_review`` starts a thread like the real one."""

    def __init__(self, messages, review_seconds=0.2, tools=("memory", "skill_manage")):
        self.session_id = "s_learn"
        self.valid_tool_names = set(tools)
        self._memory_store = object()
        self._session_messages = messages
        self._delegate_depth = 0
        self.review_seconds = review_seconds
        self.spawned = []
        self.finished = threading.Event()

    def _spawn_background_review(self, *, messages_snapshot, review_memory=False, review_skills=False):
        self.spawned.append((len(messages_snapshot), review_memory, review_skills))

        def _review():
            time.sleep(self.review_seconds)
            self.finished.set()

        self._background_review_thread = threading.Thread(target=_review, daemon=True, name="bg-review")
        self._background_review_thread.start()


def test_worker_turn_defers_the_review_instead_of_spawning_a_thread_that_dies(kanban_worker):
    agent = _make_agent()
    _stub_agent_for_finalize(agent)
    _run_finalize(agent)
    agent._spawn_background_review.assert_not_called()
    assert agent._exit_review_due is True


def test_turns_outside_a_worker_still_spawn_the_review(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_learn")
    monkeypatch.setattr(bre, "_exit_review_task", "")  # worker entry never deferred
    agent = _make_agent()
    _stub_agent_for_finalize(agent)
    _run_finalize(agent)
    agent._spawn_background_review.assert_called_once()


def test_delegate_child_in_a_worker_does_not_defer(kanban_worker):
    agent = SimpleNamespace(_delegate_depth=1)
    assert bre.reviews_deferred_to_exit(agent) is False


def test_exit_review_runs_over_the_whole_session_and_waits_for_it():
    agent = _ReviewingAgent(_session("terminal", "read_file", "patch", "kanban_complete"))
    outcome = bre.run_worker_exit_review(agent, wait_seconds=5, min_tool_calls=3)
    assert outcome == "complete"
    assert agent.finished.is_set(), "exit returned before the review finished"
    assert agent.spawned == [(3, True, True)]


def test_exit_review_runs_when_the_nudge_came_due_even_below_the_minimum():
    agent = _ReviewingAgent(_session("terminal"))
    agent._exit_review_due = True
    assert bre.run_worker_exit_review(agent, wait_seconds=5, min_tool_calls=3) == "complete"
    assert agent._exit_review_due is False


def test_board_bookkeeping_alone_is_not_worth_a_review():
    agent = _ReviewingAgent(_session("kanban_show", "terminal", "kanban_complete"))
    assert bre.run_worker_exit_review(agent, wait_seconds=5, min_tool_calls=3) == "not_due"
    assert agent.spawned == []


def test_exit_wait_is_bounded():
    agent = _ReviewingAgent(_session("terminal", "read_file", "patch"), review_seconds=5)
    started = time.monotonic()
    assert bre.run_worker_exit_review(agent, wait_seconds=0.3, min_tool_calls=3) == "timed_out"
    assert time.monotonic() - started < 2


def test_zero_wait_turns_the_exit_review_off():
    agent = _ReviewingAgent(_session("terminal", "read_file", "patch"))
    assert bre.run_worker_exit_review(agent, wait_seconds=0, min_tool_calls=3) == "off"
    assert agent.spawned == []


def test_session_without_memory_or_skill_tools_is_not_reviewed():
    agent = _ReviewingAgent(_session("terminal", "read_file", "patch"), tools=("terminal",))
    assert bre.run_worker_exit_review(agent, wait_seconds=5, min_tool_calls=3) == "not_started"
    assert agent.spawned == []


def test_skill_only_session_reviews_skills_only():
    agent = _ReviewingAgent(_session("terminal", "read_file", "patch"), tools=("skill_manage",))
    bre.run_worker_exit_review(agent, wait_seconds=5, min_tool_calls=3)
    assert agent.spawned == [(3, False, True)]


def test_spawn_records_the_review_thread_for_joiners(monkeypatch):
    """run_agent keeps the started bg-review thread on the agent so exit paths can join it."""
    import agent.background_review as br
    import run_agent

    agent = run_agent.AIAgent.__new__(run_agent.AIAgent)
    ran = threading.Event()
    monkeypatch.setattr(br, "prepare_background_review_run", lambda _a: object())
    monkeypatch.setattr(br, "spawn_background_review_thread", lambda *a, **k: (ran.set, "prompt"))
    monkeypatch.setattr(run_agent.AIAgent, "_maybe_requeue_preempted_review", lambda *a, **k: None)
    agent._spawn_background_review_now(messages_snapshot=[], review_skills=True)
    thread = agent._background_review_thread
    assert isinstance(thread, threading.Thread) and thread.name == "bg-review"
    thread.join(2)
    assert ran.is_set()





def test_one_shot_parent_without_skill_manage_still_reviews_skills():
    """chat -q hides skill_manage from the worker but keeps skill_view (agent/oneshot_footprint.py)."""
    agent = _ReviewingAgent(_session("terminal", "read_file", "patch"), tools=("skill_view",))
    bre.run_worker_exit_review(agent, wait_seconds=5, min_tool_calls=3)
    assert agent.spawned == [(3, False, True)]


def test_review_fork_of_a_one_shot_parent_gets_skill_manage_back():
    fork = SimpleNamespace(tools=[{"type": "function", "function": {"name": "skill_view"}}],
                           valid_tool_names={"skill_view"})
    bre.offer_skill_manage(fork)
    assert "skill_manage" in fork.valid_tool_names
    assert [t["function"]["name"] for t in fork.tools] == ["skill_view", "skill_manage"]


@pytest.mark.parametrize("names", [{"terminal"}, {"skill_view", "skill_manage"}])
def test_review_fork_tools_are_untouched_otherwise(names):
    fork = SimpleNamespace(tools=[], valid_tool_names=set(names))
    bre.offer_skill_manage(fork)
    assert fork.valid_tool_names == names and fork.tools == []
