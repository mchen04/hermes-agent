"""Channel-override phase of ``GatewayTurnMixin._resolve_session_agent_runtime``.

``platforms.<name>.channel_overrides[<id>]`` may pin a model and/or provider for a channel (threads inherit
via the parent-channel lookup). A channel's own ``fallback_providers`` chain takes the turn at resolution
time when its provider cannot resolve (e.g. every allowed pool key is benched); the switch notice is
latched once per gateway process until the channel's provider resolves again.

Split out of ``gateway/run_turn.py``. ``gateway.run`` names are read at call time so tests that patch
``gateway.run._resolve_runtime_agent_kwargs_for_provider`` / ``_get_channel_override`` still apply.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def apply_channel_route(runner: Any, source: Any, model: str, runtime_kwargs: dict) -> tuple[str, dict]:
    """``(model, runtime_kwargs)`` after this source's channel override, unchanged when there is none."""
    from gateway.run import _get_channel_override, _resolve_runtime_agent_kwargs_for_provider
    cfg = getattr(runner, "config", None)  # getattr: bare object.__new__ test runners
    if not cfg or source is None:
        return model, runtime_kwargs
    ch = _get_channel_override(
        cfg, source.platform, str(source.chat_id) if source.chat_id else "",
        thread_id=str(source.thread_id) if getattr(source, "thread_id", None) else None,
        parent_id=str(source.parent_chat_id) if getattr(source, "parent_chat_id", None) else None,
    )
    if not ch:
        return model, runtime_kwargs
    if ch.model:
        model = ch.model
    if not ch.provider:
        return model, runtime_kwargs
    runtime_kwargs = _resolve_runtime_agent_kwargs_for_provider(
        ch.provider, target_model=model or None, fallback_chain=ch.fallback_providers or None)
    ch_runtime_model = runtime_kwargs.pop("model", None)
    notice = runtime_kwargs.pop("_fallback_notice", None)
    _note_channel_route_fallback(runner, source, ch, notice)
    if notice:
        # The channel's provider could not resolve and its own fallback chain took the turn: send the
        # fallback's model.
        return ch_runtime_model or model, runtime_kwargs
    # Adopt the provider's bundled model only when the override named none.
    return (ch_runtime_model if ch_runtime_model and not ch.model else model), runtime_kwargs


def _note_channel_route_fallback(runner: Any, source: Any, ch: Any, notice: Optional[str]) -> None:
    """Surface a channel route's resolution-time fallback once per gateway process, not every turn.

    A depleted API-key pool keeps the channel on its fallback until the bench lifts, which can be a day
    or more; repeating the switch line on every reply would be noise. The latch clears when the
    channel's own provider resolves again, so a later switch is announced again."""
    latch = getattr(runner, "_channel_route_fallback_noticed", None)
    if latch is None:
        latch = runner._channel_route_fallback_noticed = set()
    key = (str(getattr(getattr(source, "platform", None), "value", "") or ""),
           str(ch.provider or ""), str(ch.model or ""))
    if notice is None:
        latch.discard(key)
        return
    logger.info("Channel route %s/%s unavailable; using channel fallback (%s)", ch.provider, ch.model, notice)
    if key not in latch:
        latch.add(key)
        runner._pre_agent_fallback_notice = notice
