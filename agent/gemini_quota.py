"""Gemini quota evidence: explicit daily limits, Pacific resets and shared pool state."""
from datetime import datetime, time as day_time, timedelta
import re
import time
from zoneinfo import ZoneInfo

DAILY_REASON = 'gemini_daily_quota'
_DAILY = re.compile(r'per[ _-]*day|daily[ _-]*(?:(?:request|token)[ _-]*)?(?:quota|limit)[^.;\n]{0,40}(?:exceeded|exhausted)', re.I)


def error_context(error):
    """Read structured QuotaFailure before generic exception text is truncated."""
    details = getattr(error, 'details', {}) or {}
    if getattr(error, 'status_code', None) != 429:
        return {}
    if details.get('reason') == DAILY_REASON:
        return {'reason': DAILY_REASON, 'message': 'Gemini daily quota exhausted', 'reset_at': details.get('reset_at')}
    violations = details.get('quota_violations', [])
    identities = [str(v.get('quotaId', '')) + ' ' + str(v.get('quotaMetric', ''))
                  for v in violations if isinstance(v, dict)]
    if not any(_DAILY.search(s) for s in (identities or [str(details.get('message', ''))])):
        return {}
    result = {'reason': DAILY_REASON, 'message': 'Gemini daily quota exhausted'}
    consumer = str((details.get('metadata') or {}).get('consumer', ''))
    if re.fullmatch(r'projects/[A-Za-z0-9_-]+', consumer):
        result['quota_project'] = consumer
    return result


def normalize_context(provider, status_code, context, *, now=None):
    result = dict(context)
    if provider != 'gemini' or status_code != 429:
        return result
    if result.get('reason') != DAILY_REASON and not _DAILY.search(str(result.get('message', ''))):
        return result
    now = time.time() if now is None else now
    local = datetime.fromtimestamp(now, ZoneInfo('America/Los_Angeles'))
    midnight = datetime.combine(local.date() + timedelta(days=1), day_time(), tzinfo=local.tzinfo).timestamp()
    # RetryInfo often describes minute recovery even alongside an exhausted daily quota.
    result.update(reason=DAILY_REASON, reset_at=max(midnight, result.get('reset_at') or 0))
    return result


def sync_pool_cooldowns(pool):
    """A live pool must honor peer cooldowns before sending with a cached key."""
    from agent.credential_pool import PooledCredential, read_credential_pool, auth_mod
    persisted = {r['id']: r for r in read_credential_pool('gemini') if isinstance(r, dict) and r.get('id')}
    for entry in list(pool._entries):
        row = entry.to_dict()
        merged = auth_mod._merge_disk_cooldown_state(row, persisted.get(entry.id), 'gemini')
        if merged != row:
            pool._replace_entry(entry, PooledCredential.from_dict('gemini', merged))


def pool_daily_exhausted(pool):
    """Every usable key must have daily evidence; an all-rejected pool cannot qualify."""
    if pool is None or pool.provider != 'gemini' or pool.has_available():
        return False
    from agent.credential_pool import STATUS_DEAD, STATUS_EXHAUSTED
    rows = [entry for entry in pool.entries() if entry.last_status != STATUS_DEAD]
    return bool(rows) and all(e.last_status == STATUS_EXHAUSTED and e.last_error_reason == DAILY_REASON
                             and (e.last_error_reset_at or 0) > time.time() for e in rows)
