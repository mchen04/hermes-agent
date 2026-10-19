"""Compaction window planning for ContextCompressor: the head/middle/tail boundaries that compress() and
the opt-in prepared-compaction pass (agent/prepared_compaction.py) both plan, and the splice of a prepared
summary into that window."""

from __future__ import annotations

import logging

from typing import Any, Optional

from agent.context_compressor_landing import (
    LANDING_TAIL_FLOOR_TOKENS, LandingPlan, next_tail_budget, planned_tail_budget,
)

from agent.prepared_compaction import PreparedCompaction, PreparedSummary

logger = logging.getLogger(__name__)


class CompactionWindowMixin:
    """Window planning shared by compress() and the background pass."""

    # Set by agent_init when compression.prepare_ahead is on; None leaves compress() inline as before.
    prepared_compaction: Optional[PreparedCompaction] = None

    def _compress_window(self, messages: list[dict[str, Any]], token_budget: int | None = None) -> tuple[int, int]:
        """Return ``(compress_start, compress_end)`` for the summarizable middle."""
        compress_start = self._align_boundary_forward(messages, self._protect_head_size(messages))
        compress_end = (
            self._find_tail_cut_by_tokens(messages, compress_start) if token_budget is None
            else self._find_tail_cut_by_tokens(messages, compress_start, token_budget)
        )
        # A role collision can merge the summary into the first tail row; keep an actionable user
        # event out of that slot by retaining an older assistant/tool bridge.
        latest_actionable_idx = self._find_last_user_message_idx(messages, 0)
        if compress_end == latest_actionable_idx:
            bridge_idx = latest_actionable_idx - 1
            bridge_role = messages[bridge_idx].get("role") if bridge_idx >= 0 else None
            if bridge_role == "tool":
                bridge_idx = self._align_boundary_backward(messages, latest_actionable_idx)
            elif bridge_role != "assistant":
                bridge_idx = -1
            if bridge_idx > compress_start:
                compress_end = bridge_idx
        return compress_start, compress_end

    def _plan_landing(self, messages: list[dict[str, Any]]) -> LandingPlan:
        target = self.landing_target_tokens
        reserve = self._summary_reserve_tokens(messages)
        budget, shrinkable = planned_tail_budget(
            self.tail_token_budget, target, self.real_token_ratio, self._real_overhead_tokens(),
            self.request_overhead_tokens, reserve,
        )
        while True:
            pruned, pruned_count = self._prune_old_tool_results(
                messages, protect_tail_count=self.protect_last_n, protect_tail_tokens=budget,
            )
            pruned = self._drop_blank_echoes(pruned)
            start, end = self._compress_window(pruned, None if budget == self.tail_token_budget else budget)
            if start < end and getattr(self, "tail_mode", "lean") == "lean":
                pruned = self._demote_stale_tail_tools(pruned, end)
            projected = self._landing_tokens(self._messages_rough(pruned[:start] + pruned[end:]) + reserve) if target else 0
            plan = LandingPlan(pruned, pruned_count, start, end, budget, projected)
            if not target or projected <= target:
                plan.outcome = "target_met" if target else "disabled"
                break
            if not shrinkable or start >= end or budget <= LANDING_TAIL_FLOOR_TOKENS:
                plan.outcome = "floor_exceeds_target"
                plan.floor_name = (
                    "fixed_prompt" if self._real_overhead_tokens() >= target else
                    "summary_reserve" if not shrinkable else "protected_context"
                )
                plan.floor_tokens = (
                    self._landing_tokens(reserve) if not shrinkable else projected
                )
                logger.info(
                    "Compaction landing target %d cannot fit: %s floor %d real tokens",
                    target, plan.floor_name, plan.floor_tokens,
                )
                break
            budget = next_tail_budget(budget)
        self._last_landing_outcome = plan.outcome
        self._last_landing_floor_tokens = plan.floor_tokens
        return plan

    def _plan_compaction_window(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int, int]:
        """Phases 1-2 of compress(): ``(working, pruned_count, compress_start, compress_end)``.
        ``working`` is a pruned copy without blank echoes; the boundaries index into it."""
        plan = self._plan_landing(messages)
        working, pruned_count = plan.messages, plan.pruned_count
        compress_start, compress_end = plan.compress_start, plan.compress_end
        from agent.context_compressor import _collect_live_skill_names

        self._live_skill_names = _collect_live_skill_names(
            working[:compress_start] + working[compress_end:], getattr(self, "_live_skill_source_text", ""),
        )
        return working, pruned_count, compress_start, compress_end

    def _take_prepared_summary(
        self, messages: list[dict[str, Any]], compress_start: int, *, fresh: bool, provider_context: bool,
    ) -> Optional[PreparedSummary]:
        """The summary prepared in the background for this window, which an automatic compaction splices at
        ITS boundary; every newer message stays outside the summary, in the tail. ``fresh`` compactions
        (manual, focus, overflow recovery, memory-provider context) summarize anew, and once a memory
        provider supplies context no further summaries are prepared (each would be thrown away)."""
        if self.prepared_compaction is None:
            return None
        if provider_context:
            self.prepared_compaction.stop_for_provider_context()
        prepared = self.prepared_compaction.take(
            messages, self._session_id, compress_start, eligible=not (fresh or provider_context),
        )
        if prepared is not None and self.landing_target_tokens:
            retained = messages[:compress_start] + messages[prepared.compress_end:]
            projected = self._landing_tokens(self._messages_rough(
                retained + [{"role": "user", "content": prepared.summary}],
            ))
            if projected > self.landing_target_tokens:
                self.prepared_compaction.discard("landing target exceeded by prepared tail")
                return None
        return prepared

    def _adopt_prepared_summary(self, prepared: PreparedSummary) -> str:
        """Adopt a prepared summary as an inline summary call would have left it; returns the handoff text."""
        from agent.prepared_compaction import adopt

        adopt(self, prepared)
        return prepared.summary
