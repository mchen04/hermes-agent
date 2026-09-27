"""LOCAL-PATCH learn-failed-cron: a failed cron agent run gets a background review; a good run does not.

Cron builds its agent with ``skip_background_review=True`` (the review costs ~30K tokens per
run). A failed run is worth learning from, so after delivery the scheduler starts a review and
tears the agent down only once that review ends (teardown closes what the review uses).
"""

from __future__ import annotations

import threading

import pytest

import cron.scheduler as s


class _CronAgent:
    def __init__(self, tools=("memory", "skill_manage")):
        self.session_id = "cron_job1_x"
        self.valid_tool_names = set(tools)
        self._memory_store = object()
        self._delegate_depth = 0
        self._session_messages = [
            {"role": "user", "content": "check the feed"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "1", "type": "function", "function": {"name": "terminal", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "1", "content": "curl: (6) could not resolve host"},
            {"role": "assistant", "content": "[CRON_FAILURE]\nthe feed host did not resolve"},
        ]
        self.release_review = threading.Event()
        self.reviews = []

    def _spawn_background_review(self, *, messages_snapshot, review_memory=False, review_skills=False):
        self.reviews.append((len(messages_snapshot), review_memory, review_skills))
        self._background_review_thread = threading.Thread(
            target=self.release_review.wait, args=(5,), daemon=True, name="bg-review")
        self._background_review_thread.start()


@pytest.fixture
def run_env(monkeypatch, tmp_path):
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    state = {"teardown": threading.Event(), "torn_down": []}
    monkeypatch.setattr(s, "create_execution", lambda *_a, **_kw: {"id": "exec-t"})
    monkeypatch.setattr(s, "claim_dispatch", lambda _job_id: True)
    monkeypatch.setattr(s, "mark_execution_running", lambda _execution_id: {})
    monkeypatch.setattr(s, "save_job_output", lambda jid, out: f"/tmp/{jid}.txt")
    monkeypatch.setattr(s, "mark_job_run", lambda *a, **kw: True)
    monkeypatch.setattr(s, "finish_execution", lambda *a, **kw: None)
    monkeypatch.setattr(s, "_upsert_incident_for_failure", lambda *_a, **_kw: (False, None))
    monkeypatch.setattr(s, "load_config", lambda: {})

    def _teardown(agent, job_id, **_kw):
        state["torn_down"].append(agent)
        state["teardown"].set()

    monkeypatch.setattr(s, "_teardown_cron_agent", _teardown)
    return state


def _run(monkeypatch, agent, outcome):
    """Run one job whose run_job hands back ``agent`` with ``outcome`` = (success, final, error)."""
    success, final, error = outcome

    def _fake_run_job(job, *, defer_agent_teardown=None, **_kw):
        defer_agent_teardown.append(agent)
        return success, "doc", final, error

    monkeypatch.setattr(s, "run_job", _fake_run_job)
    return s.run_one_job({"id": "job1", "name": "feed", "deliver": "local"})


@pytest.mark.parametrize("outcome", [
    (False, "", "RuntimeError: agent reported failure"),          # run_job failure path
    (True, "[CRON_FAILURE]\nthe feed host did not resolve", None),  # agent-declared failure
    (True, "", None),                                              # empty-reply soft failure
], ids=["error", "cron_failure_marker", "empty_reply"])
def test_failed_run_is_reviewed_before_teardown(run_env, monkeypatch, outcome):
    agent = _CronAgent()
    assert _run(monkeypatch, agent, outcome) is True
    assert agent.reviews == [(4, True, True)]
    # The review is still running: the agent must still be alive.
    assert not run_env["teardown"].wait(0.3), "agent torn down under a running review"
    agent.release_review.set()
    assert run_env["teardown"].wait(5), "agent never torn down after its review"
    assert run_env["torn_down"] == [agent]


def test_successful_run_is_not_reviewed(run_env, monkeypatch):
    agent = _CronAgent()
    _run(monkeypatch, agent, (True, "all good", None))
    assert agent.reviews == []
    assert run_env["torn_down"] == [agent]


def test_failed_run_without_learning_tools_tears_down_at_once(run_env, monkeypatch):
    agent = _CronAgent(tools=("clarify",))
    _run(monkeypatch, agent, (False, "", "RuntimeError: boom"))
    assert agent.reviews == []
    assert run_env["torn_down"] == [agent]


def test_shutdown_interrupted_run_is_not_reviewed(run_env, monkeypatch):
    agent = _CronAgent()
    monkeypatch.setattr(s, "_is_interrupted", lambda *_a, **_k: True)
    _run(monkeypatch, agent, (True, "partial text", None))
    assert agent.reviews == []
    assert run_env["torn_down"] == [agent]


def test_review_can_be_turned_off(run_env, monkeypatch):
    (s._get_hermes_home() / "config.yaml").write_text("cron:\n  review_failed_runs: false\n")
    agent = _CronAgent()
    _run(monkeypatch, agent, (False, "", "RuntimeError: boom"))
    assert agent.reviews == []
    assert run_env["torn_down"] == [agent]


def test_review_holds_its_own_state_db_reference(run_env, monkeypatch):
    """run_job released the run's state.db before delivery; the review books usage on that session,
    so it re-acquires the store and releases it only after teardown."""
    import hermes_state_registry as reg

    events = []
    fresh = type("DB", (), {"db_path": "/x/state.db"})()
    monkeypatch.setattr(reg, "acquire", lambda path: events.append(("acquire", path)) or fresh)
    monkeypatch.setattr(reg, "release_or_close", lambda db: events.append(("release", db)))
    agent = _CronAgent()
    agent._session_db = type("DB", (), {"db_path": "/x/state.db"})()
    _run(monkeypatch, agent, (False, "", "RuntimeError: boom"))
    assert events == [("acquire", "/x/state.db")] and agent._session_db is fresh
    agent.release_review.set()
    assert run_env["teardown"].wait(5)
    for _ in range(50):
        if len(events) == 2:
            break
        threading.Event().wait(0.02)
    assert events[-1] == ("release", fresh)
