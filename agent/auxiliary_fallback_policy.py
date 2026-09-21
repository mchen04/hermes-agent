"""Opt-in failure conditions for an authoritative auxiliary fallback chain."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)
# LOCAL-PATCH auxiliary-strict-fallback: configured helpers never inherit an implicit main route.
_TRIGGERS = frozenset({"daily_quota", "server_unavailable"})
_OUTAGE_REASON = "server unavailable after retries"


def _daily_limit(message: str) -> bool:
    text = re.sub(r"[\s_-]+", "", message.lower())
    return any(marker in text for marker in ("dailyquota", "dailylimit", "perday"))


def task_fallback_policy(task: str | None) -> frozenset[str] | None:
    """None preserves legacy routing; a configured list permits only those chain triggers."""
    if not task:
        return None
    from agent.auxiliary_client import _get_auxiliary_task_config

    config = _get_auxiliary_task_config(task)
    raw = config.get("fallback_on")
    if raw is None:
        return None
    if not isinstance(raw, list) or any(not isinstance(value, str) or value not in _TRIGGERS for value in raw):
        raise ValueError(f"auxiliary.{task}.fallback_on must list daily_quota or server_unavailable")
    if str(config.get("provider") or "auto").strip().lower() in {"", "auto", "main"}:
        raise ValueError(f"auxiliary.{task}.fallback_on requires an explicit provider")
    return frozenset(raw)


def _allowed_failure(error: Exception, provider: str, policy: frozenset[str]) -> str | None:
    status = getattr(error, "status_code", None) or getattr(getattr(error, "response", None), "status_code", None)
    if status == 503 and "server_unavailable" in policy:
        from agent.auxiliary_client import _transient_retry_count
        return _OUTAGE_REASON if _transient_retry_count() > 0 else None
    if status != 429 or "daily_quota" not in policy:
        return None
    if not _daily_limit(str(error)):
        return None
    from agent.credential_pool import load_pool

    pool = load_pool(provider)
    # Credential recovery runs before this gate. Never switch providers while another pooled
    # credential remains usable (including a third entry beyond the recovery rung's first retry).
    if pool.has_credentials() and pool.peek() is not None:
        return None
    return "daily quota exhausted"


def cached_policy_fallback(task: str, provider: str, model: str | None, base_url: str | None,
                           policy: frozenset[str]):
    """Honor a confirmed outage or persisted daily exhaustion before sending another request."""
    from agent import auxiliary_client as aux
    from agent.credential_pool import load_pool

    reason = None
    if "server_unavailable" in policy and aux._is_provider_unhealthy(provider, base_url):
        key = aux._unhealthy_cache_key(provider, base_url)
        if aux._aux_unhealthy_reason.get(key) == _OUTAGE_REASON:
            reason = _OUTAGE_REASON
    if reason is None and "daily_quota" in policy:
        pool = load_pool(provider)
        if pool.has_credentials() and not pool.has_available():
            entries = pool.entries()
            if all(entry.last_status == "exhausted" and entry.last_error_code == 429
                   and _daily_limit(entry.last_error_message or "") for entry in entries):
                reason = "daily quota exhausted"
    if reason is None:
        return None
    candidate = aux._try_configured_fallback_chain(
        task, provider, reason=reason, failed_model=model, failed_base_url=base_url or "")
    if candidate[0] is None:
        raise aux.AuxiliaryClientUnavailable(
            f"Auxiliary {task}: {reason}; configured fallback_chain is unavailable")
    return candidate


def configured_policy_fallback(error: Exception, route: Any, policy: frozenset[str]):
    """Walk only the explicit task chain after an allowed terminal primary failure."""
    from agent import auxiliary_client as aux

    reason = _allowed_failure(error, route.resolved_provider, policy)
    if reason is None:
        logger.info("Auxiliary %s: fallback_on does not permit %s; keeping the configured route",
                    route.task, type(error).__name__)
        return None
    if reason == _OUTAGE_REASON:
        aux._mark_provider_unhealthy(route.resolved_provider, ttl=60, base_url=route.base_info,
                                     reason=_OUTAGE_REASON, level=logging.INFO)
    tried = set()
    while True:
        client, model, label = aux._try_configured_fallback_chain(
            route.task, route.resolved_provider, reason=reason,
            failed_model=route.final_model, failed_base_url=route.base_info)
        lane = (label, model, str(getattr(client, "base_url", "") or ""))
        if client is None or lane in tried:
            return None
        tried.add(lane)
        aux._record_route_info(route.route_info, aux._fallback_provider_from_label(label), model)
        response = yield aux._LadderStep("fallback", (client, model, label))
        if response is not None:
            return response
