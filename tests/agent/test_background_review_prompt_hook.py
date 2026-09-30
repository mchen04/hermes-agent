"""LOCAL-PATCH background-review-prompt-hook: a plugin may replace the review request, nothing else."""

from unittest.mock import MagicMock

from agent.background_review import _COMBINED_REVIEW_PROMPT, spawn_background_review_thread
from hermes_cli.plugins import VALID_HOOKS


def _agent():
    agent = MagicMock()
    del agent._COMBINED_REVIEW_PROMPT
    agent.session_id = "s1"
    return agent


def test_hook_is_registered():
    assert "background_review_prompt" in VALID_HOOKS


def test_hook_replaces_the_prompt_and_focus_still_appends(monkeypatch):
    calls = []

    def fake_invoke(name, **kwargs):
        calls.append((name, kwargs))
        return [None, {"prompt": "   "}, {"prompt": "Custom review."}]

    monkeypatch.setattr("hermes_cli.lifecycle.invoke_hook", fake_invoke)
    _target, prompt = spawn_background_review_thread(
        _agent(), [], review_memory=True, review_skills=True, task_cfg={}, focus="the tests")
    assert prompt.startswith("Custom review.") and prompt.endswith("the tests")
    name, kwargs = calls[0]
    assert name == "background_review_prompt"
    assert kwargs == {"prompt": _COMBINED_REVIEW_PROMPT, "review_memory": True, "review_skills": True,
                      "explicit": False, "session_id": "s1"}


def test_no_hook_result_keeps_the_default_prompt(monkeypatch):
    monkeypatch.setattr("hermes_cli.lifecycle.invoke_hook", lambda name, **kwargs: [])
    _target, prompt = spawn_background_review_thread(
        _agent(), [], review_memory=True, review_skills=True, task_cfg={})
    assert prompt == _COMBINED_REVIEW_PROMPT
