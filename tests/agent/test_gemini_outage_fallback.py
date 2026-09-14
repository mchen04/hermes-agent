import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from tests.agent.test_gemini_route_contract import POLICY, seed


def configure(tmp_path, monkeypatch, enabled=True):
    root = seed(tmp_path, monkeypatch)
    policy = dict(POLICY, outages=enabled)
    (root / 'config.yaml').write_text(json.dumps({'quota_fallbacks': [policy], 'auxiliary': {
        'transient_retries': 2, 'title_generation': {
            'provider': 'gemini', 'model': POLICY['model'], 'reasoning_effort': 'low'}}}))
    return root


@pytest.mark.parametrize('async_mode', [False, True])
@pytest.mark.parametrize('enabled,recover', [(True, False), (True, True), (False, False)])
def test_aux_outage_waits_for_existing_retries_and_returns_to_primary(tmp_path, monkeypatch, async_mode, enabled, recover):
    from agent import auxiliary_client as aux, credential_pool as cp
    root = configure(tmp_path, monkeypatch, enabled)
    auth_before = (root / 'auth.json').read_bytes()
    aux._client_cache.clear()
    monkeypatch.setattr(aux, '_TRANSIENT_RETRY_BACKOFF_BASE', 0)
    calls = []
    healthy = [False]

    def wire(client, request, **kwargs):
        assert request.url.host == 'generativelanguage.googleapis.com'
        calls.append(('gemini', json.loads(request.content)))
        okay = healthy[0] or (recover and len(calls) == 2)
        body = {'candidates': [{'content': {'parts': [{'text': 'primary'}]}}]} if okay else {
            'error': {'status': 'UNAVAILABLE', 'message': 'This model is currently experiencing high demand.'}}
        return httpx.Response(200 if okay else 503, request=request, json=body)

    monkeypatch.setattr(httpx.Client, 'send', wire)

    def luna_create(**kwargs):
        calls.append(('luna', kwargs))
        assert kwargs['model'] == POLICY['fallback']['model']
        assert kwargs['extra_body']['reasoning']['effort'] == 'low'
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='fallback', tool_calls=[]), finish_reason='stop')])

    luna = SimpleNamespace(api_key='dummy-luna', base_url='https://chatgpt.com/backend-api/codex',
        chat=SimpleNamespace(completions=SimpleNamespace(create=luna_create)))
    if async_mode:
        async def async_create(**kwargs):
            return luna_create(**kwargs)
        luna.chat.completions.create = async_create
        luna.HERMES_SKIP_ASYNC_WRAP = True
    monkeypatch.setattr(aux, '_build_codex_client', lambda model: (luna, model))
    original_pool = aux.load_pool
    monkeypatch.setattr(aux, 'load_pool', lambda provider: cp.CredentialPool(provider, []) if provider == 'openai-codex' else original_pool(provider))
    route = {}
    args = dict(task='title_generation', messages=[{'role': 'user', 'content': 'Synthetic.'}],
                tools=[], max_tokens=64, timeout=5, route_info=route)

    def invoke():
        return asyncio.run(aux.async_call_llm(**args)) if async_mode else aux.call_llm(**args)

    if not enabled:
        with pytest.raises(Exception):
            invoke()
        assert [c[0] for c in calls] == ['gemini'] * (2 if async_mode else 3)
    elif recover:
        assert invoke().choices[0].message.content == 'primary'
        assert [c[0] for c in calls] == ['gemini', 'gemini']
    else:
        assert invoke().choices[0].message.content == 'fallback'
        assert [c[0] for c in calls] == ['gemini'] * (2 if async_mode else 3) + ['luna']
        assert route == {'provider': 'openai-codex', 'model': POLICY['fallback']['model']}
        assert invoke().choices[0].message.content == 'fallback'
        assert calls[-2][0] == calls[-1][0] == 'luna'
        from agent import gemini_outage
        now = gemini_outage.time.monotonic()
        monkeypatch.setattr(gemini_outage.time, 'monotonic', lambda: now + 61)
        healthy[0] = True
        assert invoke().choices[0].message.content == 'primary'
        assert route['provider'] == 'gemini'
    assert (root / 'auth.json').read_bytes() == auth_before


@pytest.mark.parametrize('surface', ['cli', 'cron'])
def test_real_agent_outage_exhausts_budget_preserves_prefix_and_restores(tmp_path, monkeypatch, surface):
    import requests
    from agent import auxiliary_client as aux, credential_pool as cp
    from agent.agent_runtime_helpers import restore_primary_runtime
    from run_agent import AIAgent
    root = configure(tmp_path, monkeypatch)
    auth_before = (root / 'auth.json').read_bytes()
    monkeypatch.setattr(requests.sessions.Session, 'request', lambda *a, **kw: (_ for _ in ()).throw(requests.ConnectionError('offline metadata')))
    monkeypatch.setattr(aux, '_read_codex_access_token', lambda: 'dummy-luna')
    monkeypatch.setattr('agent.turn_api_error.compute_error_backoff', lambda *a, **kw: 0)
    calls = []

    def wire(client, request, **kwargs):
        body = json.loads(request.content)
        calls.append((request.url.host, body))
        if request.url.host == 'generativelanguage.googleapis.com':
            return httpx.Response(503, request=request, json={'error': {'status': 'UNAVAILABLE',
                'message': 'This model is currently experiencing high demand.'}})
        assert request.url.host == 'chatgpt.com' and body['model'] == POLICY['fallback']['model']
        assert body['reasoning']['effort'] == 'low'
        response = {'id': 'synthetic', 'object': 'response', 'status': 'completed', 'model': body['model'], 'output': [
            {'id': 'msg_dummy', 'type': 'message', 'role': 'assistant', 'status': 'completed',
             'content': [{'type': 'output_text', 'text': 'Recovered.', 'annotations': []}]}],
            'usage': {'input_tokens': 2, 'output_tokens': 2, 'total_tokens': 4}}
        events = [{'type': 'response.output_text.delta', 'delta': 'Recovered.', 'output_index': 0,
                   'content_index': 0, 'item_id': 'msg_dummy'}, {'type': 'response.completed', 'response': response}]
        content = ''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e) + '\n\n' for e in events)
        return httpx.Response(200, request=request, content=content, headers={'Content-Type': 'text/event-stream'})

    monkeypatch.setattr(httpx.Client, 'send', wire)
    if surface == 'cron':
        from cron.scheduler import _resolve_job_runtime, _CronJobConfig
        config = json.loads((root / 'config.yaml').read_text())
        runtime, model = _resolve_job_runtime({'id': 'dummy', 'provider': 'gemini'}, 'dummy',
            _CronJobConfig(cfg=config, model=POLICY['model'], model_cfg={}, cron_default_provider=''))
    else:
        from hermes_cli.runtime_provider import resolve_runtime_provider
        runtime = resolve_runtime_provider(requested='gemini', target_model=POLICY['model'])
        model = POLICY['model']
    agent = AIAgent(provider=runtime['provider'], model=model, api_key=runtime['api_key'], base_url=runtime['base_url'],
        credential_pool=runtime['credential_pool'], skip_memory=True, skip_context_files=True,
        enabled_toolsets=[], quiet_mode=True, max_iterations=4, reasoning_config={'effort': 'low'})
    agent._cached_system_prompt = 'Immutable synthetic Gemini prefix.'
    try:
        result = agent.run_conversation('Return a greeting.')
        assert result['final_response'] == 'Recovered.'
        assert [host for host, _ in calls] == ['generativelanguage.googleapis.com'] * agent._api_max_retries + ['chatgpt.com']
        assert (agent.provider, agent.model) == ('openai-codex', POLICY['fallback']['model'])
        assert agent._cached_system_prompt == 'Immutable synthetic Gemini prefix.'
        assert restore_primary_runtime(agent) is False
        deadline = agent._rate_limited_until
        monkeypatch.setattr(cp.time, 'monotonic', lambda: deadline + 1)
        assert restore_primary_runtime(agent) is True
        assert (agent.provider, agent.model) == ('gemini', POLICY['model'])
        assert agent._cached_system_prompt == 'Immutable synthetic Gemini prefix.'
        assert (root / 'auth.json').read_bytes() == auth_before
    finally:
        agent.client.close()


@pytest.mark.parametrize('status', [400, 401, 403, 429, 500, 502, 504])
@pytest.mark.parametrize('async_mode', [False, True])
def test_outage_policy_rejects_other_http_failures(tmp_path, monkeypatch, status, async_mode):
    from agent import auxiliary_client as aux
    configure(tmp_path, monkeypatch)
    aux._client_cache.clear()
    monkeypatch.setattr(aux, '_TRANSIENT_RETRY_BACKOFF_BASE', 0)
    calls = []

    def wire(client, request, **kwargs):
        assert request.url.host == 'generativelanguage.googleapis.com'
        calls.append(request)
        return httpx.Response(status, request=request, json={'error': {
            'message': 'GenerateRequestsPerMinutePerProjectPerModel exceeded' if status == 429 else 'UNAVAILABLE high demand'}})

    monkeypatch.setattr(httpx.Client, 'send', wire)
    monkeypatch.setattr(aux, '_build_codex_client', lambda model: pytest.fail('Ineligible error selected Luna'))
    args = dict(task='title_generation', messages=[{'role': 'user', 'content': 'Synthetic.'}], timeout=5)
    with pytest.raises(Exception):
        asyncio.run(aux.async_call_llm(**args)) if async_mode else aux.call_llm(**args)
    assert calls
    if status >= 500:
        assert len(calls) == (2 if async_mode else 3)


def test_outage_permit_is_scoped_and_never_granted_for_tool_or_cancel_errors(tmp_path, monkeypatch):
    from agent.gemini_native_adapter import GeminiAPIError
    from agent.gemini_outage import prepare_outage_fallback, auxiliary_outage_active, outage_seconds
    from agent.quota_fallback import configured_fallback
    root = configure(tmp_path, monkeypatch)
    agent = SimpleNamespace(provider='gemini', model=POLICY['model'],
        base_url='https://generativelanguage.googleapis.com/v1beta')
    unavailable = GeminiAPIError('high demand', status_code=503)
    prepare_outage_fallback(agent, unavailable)
    assert agent._outage_fallback_ready == 60
    for error in [RuntimeError('Tool failed: HTTP 503 UNAVAILABLE'), asyncio.CancelledError(),
                  *[GeminiAPIError('UNAVAILABLE', status_code=s) for s in (400, 401, 403, 429, 500, 504)]]:
        prepare_outage_fallback(agent, error)
        assert agent._outage_fallback_ready is None
    fb = configured_fallback(agent.provider, agent.model)
    assert auxiliary_outage_active(agent.provider, agent.model, fb, unavailable)
    assert not auxiliary_outage_active(agent.provider, agent.model, fb, GeminiAPIError('auth', status_code=401))
    assert not auxiliary_outage_active(agent.provider, 'other-model', fb)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'other-profile'))
    assert not auxiliary_outage_active(agent.provider, agent.model, fb)
    monkeypatch.setenv('HERMES_HOME', str(root))
    for provider, model, url in [('openai-codex', POLICY['model'], agent.base_url),
                                 ('gemini', 'other-model', agent.base_url),
                                 ('gemini', POLICY['model'], 'https://custom.invalid/v1')]:
        agent.provider, agent.model, agent.base_url = provider, model, url
        prepare_outage_fallback(agent, unavailable)
        assert agent._outage_fallback_ready is None
    config = json.loads((root / 'config.yaml').read_text())
    config['fallback_providers'] = [{'provider': 'custom', 'model': 'chosen'}]
    assert configured_fallback('gemini', POLICY['model'], config=config) is None
    assert outage_seconds({'outages': False}) is None
    assert outage_seconds({'outages': True, 'outage_cooldown_seconds': 10000}) == 300
    assert outage_seconds({'outages': True, 'outage_cooldown_seconds': 0}) == 1
