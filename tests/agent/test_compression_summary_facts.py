"""LOCAL-PATCH compression-summary-facts.

A compaction summary must (1) use the configured language, (2) keep the key facts of the
newest tool results verbatim even when the summarizer only sees an elided tool result, and
(3) never call a user question resolved before a reply was sent.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import agent.context_compressor as cc
from agent.context_compressor import ContextCompressor


@pytest.fixture()
def compressor():
    with patch("agent.context_compressor.get_model_context_length", return_value=100000):
        c = ContextCompressor(model="test/model", threshold_percent=0.85, protect_first_n=2,
                              protect_last_n=2, quiet_mode=True)
        _ = c.context_length
        return c


def _call(cid, name="terminal"):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": "{}"}}]}


def _tool(cid, payload):
    return {"role": "tool", "tool_call_id": cid, "content": json.dumps(payload)}


# A long result: the serializer keeps only its head and tail, the merge state sits in the middle.
_LONG_GH = {"output": "log line\n" * 900 + json.dumps({"number": 21, "state": "MERGED",
                                                       "mergedAt": "2026-10-02T17:20:55Z"}) + "\n" + "tail\n" * 400,
            "exit_code": 0}
_TESTS = {"output": "collected 97 items\n...\n===== 97 passed in 4.10s =====\n", "exit_code": 0}


def _turns():
    return [
        {"role": "user", "content": "Merge PR 21 and tell me if the tests pass."},
        _call("c1"), _tool("c1", _TESTS),
        _call("c2"), _tool("c2", _LONG_GH),
        {"role": "assistant", "content": "Tests pass and PR 21 is merged."},
    ]


def test_facts_from_the_elided_middle_reach_the_prompt_and_the_summary(compressor, monkeypatch):
    turns = _turns()
    serialized = compressor._serialize_for_summary(turns)
    assert '"state": "MERGED"' not in serialized  # the summarizer's own copy lost it
    seen = {}

    def fake_llm(prompt, started):
        seen["prompt"] = prompt
        return "## Active State\nNo merge is evidenced.\n"
    monkeypatch.setattr(compressor, "_call_summary_llm", fake_llm)
    summary = compressor._generate_summary(turns)

    assert "KEY FACTS FROM THE MOST RECENT TOOL RESULTS" in seen["prompt"]
    merged_line = '{"number": 21, "state": "MERGED", "mergedAt": "2026-10-02T17:20:55Z"}'
    assert merged_line in seen["prompt"] and "97 passed in 4.10s" in seen["prompt"]
    facts = summary.split(cc._TOOL_FACTS_HEADING, 1)[1]
    assert merged_line in facts
    assert "exit_code=0" in facts and "===== 97 passed in 4.10s =====" in facts


def test_a_later_compaction_replaces_the_facts_section():
    first = cc.with_tool_facts_section("## Goal\nx\n", ["- [terminal] exit_code=1"])
    second = cc.with_tool_facts_section(first + "## Critical Context\nkept\n", ["- [terminal] exit_code=0"])
    assert second.count(cc._TOOL_FACTS_HEADING) == 1
    assert "exit_code=0" in second and "exit_code=1" not in second and "kept" in second
    assert cc.with_tool_facts_section("same", []) == "same"


def test_plain_prose_is_not_a_fact():
    turns = [_call("c1", "read_file"), _tool("c1", {"content": "I closed the door and merged lanes."})]
    assert cc.latest_tool_facts(turns) == []


def test_configured_language_replaces_the_follow_the_user_rule(compressor, monkeypatch):
    import hermes_cli.config as cfgmod
    monkeypatch.setattr(cfgmod, "load_config", lambda *a, **k: {"compression": {"summary_language": "English"}})
    prompt = compressor._build_summary_prompt("[USER]: Привет", 500, None, "", True)
    assert "Write the summary in English, whatever language the turns use" in prompt
    assert "do not translate or switch to English" not in prompt
    monkeypatch.setattr(cfgmod, "load_config", lambda *a, **k: {"compression": {}})
    prompt = compressor._build_summary_prompt("[USER]: Привет", 500, None, "", True)
    assert "do not translate or switch to English" in prompt


def test_unanswered_question_is_labelled_and_answered_one_is_not(compressor, monkeypatch):
    asked = {"role": "user", "content": "what's it waiting on?"}
    answered = {"role": "user", "content": "is PR 21 merged?"}
    messages = [
        answered, _call("c0"), _tool("c0", {"exit_code": 0}),
        {"role": "assistant", "content": "Yes, merged at 10:52."},
        asked, _call("c1"), _tool("c1", _TESTS),
    ]
    ids = cc.unanswered_user_turn_ids(messages, compressor._is_synthetic_compression_user_turn)
    assert ids == {id(asked)}
    # A reply that lands later (in the protected tail) answers it.
    tail_reply = messages + [{"role": "assistant", "content": "It waits on Michael's approval."}]
    assert cc.unanswered_user_turn_ids(tail_reply, compressor._is_synthetic_compression_user_turn) == set()

    seen = {}
    monkeypatch.setattr(compressor, "_call_summary_llm",
                        lambda prompt, started: seen.setdefault("prompt", prompt) and "## Goal\nx\n")
    scan = type("Scan", (), {"previous_summary_before": None, "has_user_turn_before": None})()
    compressor._summarize_window(messages, messages, scan, None, "", False)
    assert f"{cc._NO_REPLY_LABEL}: what's it waiting on?" in seen["prompt"]
    assert "[USER]: is PR 21 merged?" in seen["prompt"]
    assert "Never list them under Resolved Questions" in seen["prompt"]
    assert compressor._summary_unanswered_ids == set()  # per-call state does not leak
