"""Cooldown sizing for credential pool entries: exhaustion TTLs, provider reset-hint parsing and the
per-provider ``credential_pool_policies`` (allowed auth types, billing re-check bench).

Split out of ``agent/credential_pool.py``, which re-exports every public name here; this module must not
import ``agent.credential_pool`` at import time (``_pool_policy`` reads its config loader lazily so tests
that patch ``agent.credential_pool._load_config_safe`` still apply).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from agent.credential_pool import PooledCredential

# Cooldowns before retrying an exhausted credential. Transient 401s cool down
# briefly so single-key setups recover; 429/402/other take an hour.
# Provider-supplied reset_at timestamps override these defaults.
EXHAUSTED_TTL_401_SECONDS = 5 * 60
EXHAUSTED_TTL_429_SECONDS = 60 * 60
EXHAUSTED_TTL_DEFAULT_SECONDS = 60 * 60
# When the offending key is the sole non-DEAD entry, an hour-long bench means
# an hour of hard failures. Throttles (429/403/5xx) reset in seconds, so a sole
# credential cools down briefly instead.
EXHAUSTED_TTL_SOLE_CREDENTIAL_SECONDS = 60

# ``FailoverReason.billing`` as a bare string: the pool persists classified
# failure semantics to JSON and must not import the classifier.
FAILURE_REASON_BILLING = "billing"

# ``credential_pool_policies.<provider>.billing_recheck_hours`` is capped at one day so a depleted
# balance is re-probed at least daily (prepaid credits can refill on a billing cycle that is not the
# calendar month). A provider-declared reset hint for a billing failure is honoured when it is in
# the future and no more than BILLING_RESET_HINT_MAX_SECONDS out; it never shortens the bench
# below EXHAUSTED_TTL_DEFAULT_SECONDS, so a bogus "retry in 1s" cannot turn into a probe storm.
BILLING_RECHECK_MAX_HOURS = 24
BILLING_RESET_HINT_MAX_SECONDS = 35 * 24 * 60 * 60

# Billing verdict resting on an ambiguous body (#82154): Anthropic's "out of
# extra usage" 400 is returned both for genuine overage and for a server-side
# content-filter rejection, which leaves the credential healthy. Unverified
# billing gets the short transient cooldown; genuine depletion re-latches.
FAILURE_REASON_BILLING_UNVERIFIED = "billing_unverified"


def _exhausted_ttl(
    error_code: Optional[int],
    *,
    sole_credential: bool = False,
    failure_reason: Optional[str] = None,
) -> int:
    """Return cooldown seconds based on the HTTP status that caused exhaustion.

    *sole_credential*: the pool has nothing to rotate to, so transient
    throttles (429 and the catch-all default covering 403/5xx/unknown) are
    capped to a brief cooldown; 401 keeps its own already-short TTL.

    *failure_reason* is the classifier verdict: an OpenRouter ``key limit
    exceeded`` and an xAI spending block both arrive as 403 but are billing,
    and a 60s retry on a spent account just re-fails. Billing keeps the full
    bench regardless of status; 402 is billing by definition.
    Unverified billing (#82154) gets the short cooldown regardless of pool
    size (the credential may be healthy), unless the status is a true 402.
    """
    if error_code == 401:
        return EXHAUSTED_TTL_401_SECONDS
    base = EXHAUSTED_TTL_429_SECONDS if error_code == 429 else EXHAUSTED_TTL_DEFAULT_SECONDS
    if failure_reason == FAILURE_REASON_BILLING_UNVERIFIED and error_code != 402:
        return min(base, EXHAUSTED_TTL_SOLE_CREDENTIAL_SECONDS)
    is_billing = error_code == 402 or failure_reason == FAILURE_REASON_BILLING
    if sole_credential and not is_billing:
        return min(base, EXHAUSTED_TTL_SOLE_CREDENTIAL_SECONDS)
    return base


def _parse_absolute_timestamp(value: Any) -> Optional[float]:
    """Best-effort parse of epoch seconds / epoch ms / ISO-8601 into epoch seconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric <= 0:
            return None
        return numeric / 1000.0 if numeric > 1_000_000_000_000 else numeric
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            numeric = float(raw)
            return numeric / 1000.0 if numeric > 1_000_000_000_000 else numeric
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(raw).timestamp()
        except ValueError:
            return None
    return None


def _billing_policy_until(entry: PooledCredential) -> Optional[float]:
    """Bench end for a verified billing exhaustion under a ``billing_recheck_hours`` policy, else None.

    The provider's reset hint wins when it is plausible (future relative to the failure, within
    BILLING_RESET_HINT_MAX_SECONDS); it is floored at the default one-hour bench. Without a usable
    hint the entry re-enters rotation ``billing_recheck_hours`` after the failure — the next request
    after that is the recovery probe, so a refilled balance is detected without a timer.
    """
    is_billing = entry.last_error_code == 402 or entry.failure_reason == FAILURE_REASON_BILLING
    if not is_billing or not entry.last_status_at:
        return None
    recheck = get_pool_billing_recheck_seconds(entry.provider)
    if recheck is None:
        return None
    marked_at = float(entry.last_status_at)
    hint = _parse_absolute_timestamp(entry.last_error_reset_at)
    if hint is not None and marked_at < hint <= marked_at + BILLING_RESET_HINT_MAX_SECONDS:
        return max(hint, marked_at + EXHAUSTED_TTL_DEFAULT_SECONDS)
    return marked_at + recheck


def _pool_policy(provider: str) -> dict[str, Any]:
    """``credential_pool_policies.<provider>`` mapping from config.yaml ({} when unset/invalid)."""
    from agent import credential_pool  # late: credential_pool imports this module

    config = credential_pool._load_config_safe()
    policies = config.get("credential_pool_policies") if config else None
    policy = policies.get((provider or "").strip().lower()) if isinstance(policies, dict) else None
    return policy if isinstance(policy, dict) else {}


def get_pool_auth_types(provider: str) -> Optional[frozenset[str]]:
    """Allowed ``auth_type`` values for *provider*'s pool, or None when unrestricted.

    ``credential_pool_policies.<provider>.auth_types: [api_key]`` keeps the pool (and runtime
    resolution, see ``runtime_provider._resolve_from_pool``) on API keys only: OAuth logins such as
    a borrowed Claude Code login are neither seeded nor selected, so exhausting the keys falls
    through to the configured fallback instead of billing a subscription.
    """
    raw = _pool_policy(provider).get("auth_types")
    values = [raw] if isinstance(raw, str) else raw if isinstance(raw, (list, tuple)) else None
    if not values:
        return None
    allowed = frozenset(str(v).strip().lower().replace("-", "_") for v in values if str(v).strip())
    return allowed or None


def get_pool_billing_recheck_seconds(provider: str) -> Optional[int]:
    """Bench length for a verified billing exhaustion (``credential_pool_policies.<provider>.
    billing_recheck_hours``), or None for the default one-hour bench. A spent prepaid balance only
    refills on the provider's billing cycle, so an hourly re-probe of every depleted key is waste."""
    raw = _pool_policy(provider).get("billing_recheck_hours")
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        return None
    if not (hours > 0) or hours != hours:
        return None
    return int(min(hours, BILLING_RECHECK_MAX_HOURS) * 3600)
