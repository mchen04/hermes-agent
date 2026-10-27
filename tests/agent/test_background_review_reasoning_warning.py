from types import SimpleNamespace
from unittest.mock import Mock

from agent import background_review as review


def test_same_model_reasoning_warning_fires_once_across_agent_instances(monkeypatch, caplog):
    monkeypatch.setattr(review, "_warned_ignored_reasoning_effort", False, raising=False)
    parents = [SimpleNamespace(_emit_warning=Mock()) for _ in range(3)]
    with caplog.at_level("WARNING", logger="agent.background_review"):
        review._warn_ignored_reasoning_effort(parents[0], {})
        assert not caplog.records
        for parent in parents:
            review._warn_ignored_reasoning_effort(parent, {"reasoning_effort": "high"})
    warnings = [record.message for record in caplog.records]
    assert len(warnings) == 1
    assert "auxiliary.background_review.reasoning_effort" in warnings[0]
    assert "same-model" in warnings[0] and "prompt cache" in warnings[0]
    assert sum(parent._emit_warning.call_count for parent in parents) == 1
