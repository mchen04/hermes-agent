"""Short, process-local availability cooldowns; never quarantine Gemini credentials."""
import time
from threading import Lock

from hermes_constants import get_hermes_home

_cooldowns: dict[tuple[str, str, str], float] = {}
_lock = Lock()


def outage_seconds(policy):
    if policy.get('outages') is not True:
        return None
    try:
        return max(1, min(300, int(policy.get('outage_cooldown_seconds', 60))))
    except (TypeError, ValueError, OverflowError):
        return 60


def eligible_outage(error):
    # Only the native adapter's availability response qualifies. Text matches could
    # accidentally promote tool errors, authentication failures or minute quotas.
    from agent.gemini_native_adapter import GeminiAPIError
    return isinstance(error, GeminiAPIError) and error.status_code == 503


def auxiliary_outage_active(provider, model, fallback, exhausted_error=None):
    seconds = fallback.get('_outage_cooldown_seconds')
    if seconds is None:
        return False
    now = time.monotonic()
    key = (str(get_hermes_home()), provider, model)
    with _lock:
        for expired in [k for k, until in _cooldowns.items() if until <= now]:
            del _cooldowns[expired]
        if exhausted_error is not None:
            if not eligible_outage(exhausted_error):
                return False
            _cooldowns[key] = now + seconds
        return _cooldowns.get(key, 0) > now


def prepare_outage_fallback(agent, error):
    """Called only at retry exhaustion; early auth/rate-limit paths get no permit."""
    from agent.quota_fallback import configured_fallback
    agent._outage_fallback_ready = None
    if eligible_outage(error):
        fallback = configured_fallback(agent.provider, agent.model, base_url=agent.base_url)
        if fallback:
            agent._outage_fallback_ready = fallback.get('_outage_cooldown_seconds')
