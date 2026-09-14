"""Opt-in provider/model quota fallback, using the existing runtime and client resolvers."""
import time

from agent.gemini_quota import pool_daily_exhausted


class GeminiPoolUnavailable(RuntimeError):
    def __init__(self, pool):
        self.daily = pool_daily_exhausted(pool)
        self.reset_at = pool.next_available_at()
        super().__init__('Gemini daily quota exhausted' if self.daily else 'Gemini credentials unavailable (auth or transient cooldown)')


def configured_fallback(provider, model, *, config=None, base_url=None):
    if provider != 'gemini':
        return None
    if base_url:
        from agent.gemini_native_adapter import is_native_gemini_base_url
        if not is_native_gemini_base_url(base_url):
            return None
    if config is None:
        from hermes_cli.config import load_config_readonly
        config = load_config_readonly()
    from hermes_cli.fallback_config import get_fallback_chain
    if get_fallback_chain(config):
        return None  # An explicit fallback chain owns its route.
    for row in config.get('quota_fallbacks', []):
        if not isinstance(row, dict) or (row.get('provider'), row.get('model')) != (provider, model):
            continue
        fallback = row.get('fallback')
        if isinstance(fallback, dict) and fallback.get('provider') and fallback.get('model'):
            if (fallback['provider'], fallback['model']) != (provider, model):
                from agent.gemini_outage import outage_seconds
                return {**fallback, '_daily_quota_only': True,
                        '_outage_cooldown_seconds': outage_seconds(row)}
    return None


def resolve_exhausted_runtime(error, provider, model, *, explicit_api_key=None, explicit_base_url=None):
    if not error.daily or explicit_api_key or explicit_base_url:
        raise error
    fallback = configured_fallback(provider, model)
    if fallback is None:
        raise error
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from hermes_cli.fallback_config import resolve_entry_api_key
    runtime = resolve_runtime_provider(requested=fallback['provider'], target_model=fallback['model'],
        explicit_api_key=resolve_entry_api_key(fallback), explicit_base_url=fallback.get('base_url'))
    return {**runtime, 'model': fallback['model'], 'quota_fallback_from': {
        'provider': provider, 'model': model, 'reset_at': error.reset_at}}


def auxiliary_route(provider, model, *, api_key=None, base_url=None, task=None, exhausted_error=None):
    """Resolve before the client cache, so reset naturally reselects the primary."""
    if provider != 'gemini' or api_key or base_url:
        return None
    from agent.auxiliary_client import _get_auxiliary_task_config
    if task and _get_auxiliary_task_config(task).get('fallback_chain'):
        return None
    fallback = configured_fallback(provider, model)
    if not fallback:
        return None
    from agent.credential_pool import load_pool
    if pool_daily_exhausted(load_pool(provider)):
        return fallback
    from agent.gemini_outage import auxiliary_outage_active
    if auxiliary_outage_active(provider, model, fallback, exhausted_error):
        return fallback
    return None


def resolve_gemini_client(req):
    from agent import auxiliary_client as aux
    from agent.gemini_native_adapter import GeminiNativeClient, is_native_gemini_base_url
    from hermes_cli.runtime_provider import resolve_runtime_provider
    if (req.task and not req.explicit_api_key and not req.explicit_base_url
            and aux._get_auxiliary_task_config(req.task).get('fallback_chain')):
        from agent.credential_pool import load_pool
        if pool_daily_exhausted(load_pool('gemini')):
            return None, None  # The task's explicit chain owns unavailable-client recovery.
    runtime = resolve_runtime_provider(requested='gemini', target_model=req.model,
        explicit_api_key=req.explicit_api_key, explicit_base_url=req.explicit_base_url)
    if runtime['provider'] != 'gemini':
        fallback = configured_fallback('gemini', req.model)
        from hermes_cli.fallback_config import resolve_entry_api_key
        client, model = aux.resolve_provider_client(runtime['provider'], runtime['model'], async_mode=req.async_mode,
            raw_codex=req.raw_codex, explicit_api_key=resolve_entry_api_key(fallback),
            explicit_base_url=fallback.get('base_url'), api_mode=fallback.get('api_mode'))
        if client is not None:
            client._hermes_aux_effective_provider = runtime['provider']
            client._hermes_quota_fallback = fallback
        return client, model
    if not is_native_gemini_base_url(runtime['base_url']):
        return None
    client = GeminiNativeClient(api_key=runtime['api_key'], base_url=runtime['base_url'],
        credential_pool=runtime.get('credential_pool'))
    return aux._route_client(req, client, req.model)


def guard_native_credential(pool, api_key):
    if pool is None:
        return
    from agent.credential_pool import STATUS_DEAD, STATUS_EXHAUSTED, _exhausted_until
    from agent.gemini_native_adapter import GeminiAPIError
    if pool.provider != 'gemini':
        raise GeminiAPIError('Gemini client cannot use another provider pool', status_code=401)
    # has_available also synchronizes persisted peer cooldowns into this instance.
    pool.has_available()
    entry = next((e for e in pool.entries() if e.runtime_api_key == api_key), None)
    if entry is None:
        raise GeminiAPIError('Gemini key does not belong to the supplied pool', status_code=401)
    if entry.last_status == STATUS_DEAD:
        raise GeminiAPIError('Gemini credential was rejected; reauthentication required', status_code=401)
    if entry.last_status == STATUS_EXHAUSTED and (_exhausted_until(entry, sole_credential=pool._is_sole_credential()) or 0) > time.time():
        raise GeminiAPIError('Gemini credential remains in quota cooldown', status_code=429,
            details={'message': entry.last_error_message or '', 'reason': entry.last_error_reason,
                     'reset_at': entry.last_error_reset_at})
