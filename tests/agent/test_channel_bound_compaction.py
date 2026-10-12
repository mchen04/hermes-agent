from unittest.mock import patch

import pytest

from agent.context_compressor import ContextCompressor, SKILL_PRUNED_MARKER_PREFIX
from agent.skill_commands import _build_skill_message


@pytest.mark.parametrize("fallback", [False, True])
def test_channel_bound_skills_survive_repeated_compactions_without_prune_markers(fallback):
    bound = "\n\n".join(
        _build_skill_message(
            {"name": name, "content": f"BOUND-{name}: Keep this policy. " * 200},
            None,
            f'[IMPORTANT: The "{name}" skill is auto-loaded. Follow its instructions for this session.]',
        )
        for name in ("channel-policy", "channel-work")
    )
    with patch("agent.context_compressor.get_model_context_length", return_value=100_000):
        compressor = ContextCompressor(model="test/model", quiet_mode=True, protect_first_n=0, protect_last_n=2)
    messages = [{"role": "user", "content": bound + "\n\nPlease continue."}]
    for iteration in range(2):
        messages += [
            {"role": "assistant" if i % 2 == 0 else "user", "content": f"turn {iteration}-{i} " + "q" * 500}
            for i in range(30)
        ]
        with (
            patch.object(compressor, "_find_tail_cut_by_tokens", return_value=len(messages) - 4),
            patch.object(compressor, "_call_summary_llm", side_effect=RuntimeError("summary unavailable") if fallback else None,
                         return_value="## Goal\nContinue the work."),
        ):
            messages = compressor.compress(messages, force=True)
        joined = "\n".join(str(message.get("content", "")) for message in messages)
        assert bound in joined
        assert SKILL_PRUNED_MARKER_PREFIX not in joined
        assert "## Pruned Skills" not in joined
