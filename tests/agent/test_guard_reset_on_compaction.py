"""LOCAL-PATCH guard-reset-on-compaction: a re-read after compaction is not a no-progress loop.

A kanban worker runs its whole card as one turn. Each compaction prunes the skills it loaded and tells it
to reload them; the per-turn identical-result count used to keep growing across those boundaries, so the
fifth legitimate reload was blocked and the worker exited without completing or blocking its card.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent.tool_guardrails import ToolCallGuardrailConfig, ToolCallGuardrailController

_ARGS = {"name": "profile-workflows"}
_RESULT = '{"success": true, "name": "profile-workflows", "content": "' + "x" * 600 + '"}'


def _controller_after_identical_reads(n: int) -> ToolCallGuardrailController:
    c = ToolCallGuardrailController(ToolCallGuardrailConfig(hard_stop_enabled=True))
    for _ in range(n):
        assert c.before_call("skill_view", _ARGS).allows_execution
        c.after_call("skill_view", _ARGS, _RESULT)
    return c


def test_fifth_identical_read_blocks_within_one_context():
    c = _controller_after_identical_reads(5)
    assert c.before_call("skill_view", _ARGS).action == "block"


def test_compaction_restarts_identical_result_count():
    c = _controller_after_identical_reads(4)
    c.forget_results_after_compaction()
    c.after_call("skill_view", _ARGS, _RESULT)
    assert c.before_call("skill_view", _ARGS).allows_execution


def test_compress_context_forgets_guard_results():
    from hermes_state import SessionDB
    from agent.tool_guardrails import ToolCallGuardrailController as Controller

    with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
        from run_agent import AIAgent

        db = SessionDB(db_path=Path(tmpdir) / "test.db")
        agent = AIAgent(
            api_key="test-key", base_url="https://openrouter.ai/api/v1", model="test/model", quiet_mode=True,
            session_db=db, session_id="worker-session", skip_context_files=True, skip_memory=True,
        )
        agent.compression_in_place = False
        agent._tool_guardrails = Controller(ToolCallGuardrailConfig(hard_stop_enabled=True))
        for _ in range(5):
            agent._tool_guardrails.after_call("skill_view", _ARGS, _RESULT)
        assert agent._tool_guardrails.before_call("skill_view", _ARGS).action == "block"

        compressor = MagicMock()
        compressor.compress.return_value = [{"role": "user", "content": "[CONTEXT COMPACTION] summary"},
                                            {"role": "user", "content": "tail question"}]
        compressor.compression_count = 1
        compressor.last_prompt_tokens = 0
        compressor.last_completion_tokens = 0
        compressor._last_summary_error = None
        compressor._last_compress_aborted = False
        agent.context_compressor = compressor
        history = [{"role": "user", "content": "request " + "x" * 4000}, {"role": "assistant", "content": "y" * 4000}]
        agent._compress_context(history, "sys", approx_tokens=2000)

        assert agent._tool_guardrails.before_call("skill_view", _ARGS).allows_execution
