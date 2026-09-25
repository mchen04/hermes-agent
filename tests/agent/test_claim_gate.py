"""LOCAL-PATCH unbacked-claim-gate."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.claim_gate import build_claim_nudge, claims_change, tool_ran_since_last_user
from agent.turn_stop_gates import apply_stop_gates

# Shape of the 2026-09-24 #calendar turn: tool work, then a mid-turn follow-up, then a claim.
CALENDAR = [
    {"role": "user", "content": "add liuzzy presale sept 30 to calendar and todo"},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
    {"role": "tool", "tool_call_id": "c1", "content": "{\"action\":\"created\"}"},
    {"role": "user", "content": "can you specify that for the todo list i need to register for multiple accounts"},
]
CLAIM = "Updated the TO DO LIST item to: “Liuzzy Presale - Sign up for multiple accounts.”"


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.delenv("HERMES_CLAIM_GATE", raising=False)


@pytest.mark.parametrize("text", [CLAIM, "Added “Fill windshield cracks” to your list.", "I've sent it.",
                                  "Done. The event is on your calendar."])
def test_claims_detected(text):
    assert claims_change(text)


@pytest.mark.parametrize("text", ["Your TO DO LIST:\n- Follow up", "Nothing needed from you.",
                                  "Should I add it?", "The card was updated earlier by Forge."])
def test_non_claims_ignored(text):
    assert not claims_change(text)


def test_calendar_turn_is_caught():
    assert not tool_ran_since_last_user(CALENDAR)
    assert build_claim_nudge(final_response=CLAIM, messages=CALENDAR, has_tools=True, attempts=0)


def test_backed_claim_passes():
    backed = CALENDAR + [{"role": "assistant", "content": "", "tool_calls": [{"id": "c2"}]},
                         {"role": "tool", "tool_call_id": "c2", "content": "{\"ok\":true}"}]
    assert build_claim_nudge(final_response=CLAIM, messages=backed, has_tools=True, attempts=0) is None


def test_synthetic_user_rows_do_not_hide_earlier_tools():
    msgs = CALENDAR[:3] + [{"role": "user", "content": "verify", "_verification_stop_synthetic": True}]
    assert tool_ran_since_last_user(msgs)


def test_budget_toolless_and_env_off(monkeypatch):
    assert build_claim_nudge(final_response=CLAIM, messages=CALENDAR, has_tools=True, attempts=1) is None
    assert build_claim_nudge(final_response=CLAIM, messages=CALENDAR, has_tools=False, attempts=0) is None
    monkeypatch.setenv("HERMES_CLAIM_GATE", "0")
    assert build_claim_nudge(final_response=CLAIM, messages=CALENDAR, has_tools=True, attempts=0) is None


def test_apply_stop_gates_continues_turn_without_emitting(monkeypatch):
    monkeypatch.setattr("agent.turn_stop_gates._verify_on_stop_nudge", lambda agent: None)
    monkeypatch.setattr("agent.turn_stop_gates._pre_verify_nudge", lambda *a: None)
    monkeypatch.setattr("agent.turn_stop_gates._kanban_stop_nudge", lambda *a: None)
    emitted = []
    agent = SimpleNamespace(valid_tool_names={"terminal"}, _emit_interim_assistant_message=emitted.append,
                            _interim_content_was_streamed=lambda text: False)
    messages = [dict(m) for m in CALENDAR]
    final_msg = {"role": "assistant", "content": CLAIM}
    verdict = apply_stop_gates(agent, final_msg, final_response=CLAIM, messages=messages,
                               conversation_history=None, pending_verification_response=None,
                               pending_verification_response_previewed=False)
    assert verdict.continue_turn and verdict.final_response is None
    assert messages[-1]["_claim_gate_synthetic"] and messages[-2]["finish_reason"] == "unbacked_claim"
    assert emitted == []
    again = apply_stop_gates(agent, {"role": "assistant", "content": CLAIM}, final_response=CLAIM,
                             messages=messages, conversation_history=None,
                             pending_verification_response=None,
                             pending_verification_response_previewed=False)
    assert not again.continue_turn and again.final_response == CLAIM
