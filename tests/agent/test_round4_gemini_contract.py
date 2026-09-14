import asyncio
import json
from pathlib import Path

import httpx
import pytest

from tests.agent.test_gemini_route_contract import POLICY, seed


@pytest.mark.parametrize('other', ['daily', 'dead', 'minute', 'outage', 'available'])
def test_rejected_key_does_not_disqualify_only_usable_daily_key(tmp_path, monkeypatch, other):
    from agent import credential_pool as cp, quota_fallback
    from agent.gemini_quota import pool_daily_exhausted
    from hermes_cli import runtime_provider as rp
    root = seed(tmp_path, monkeypatch)
    (root/'config.yaml').write_text(json.dumps({'quota_fallbacks': [POLICY]}), encoding='utf-8')
    pool = cp.load_pool('gemini')
    a, b = pool.entries()
    pool.mark_exhausted_and_rotate(status_code=403, credential_id=a.id, error_context={'message': 'Rejected dummy key'})
    states = {'daily': (429, 'GenerateRequestsPerDayPerProjectPerModel exceeded'),
              'dead': (403, 'Rejected dummy key'), 'minute': (429, 'GenerateRequestsPerMinutePerProjectPerModel exceeded'),
              'outage': (503, 'Service unavailable')}
    if other in states:
        status, message = states[other]
        pool.mark_exhausted_and_rotate(status_code=status, credential_id=b.id, error_context={'message': message})
    expected = other == 'daily'
    assert pool_daily_exhausted(pool) is expected
    assert bool(quota_fallback.auxiliary_route('gemini', POLICY['model'])) is expected
    seen = []
    original = rp._ladder_rungs
    def ladder(provider, key, url, model):
        if provider == 'openai-codex':
            seen.append(provider)
            yield {'provider': provider, 'api_key': 'dummy-luna', 'base_url': 'https://chatgpt.com/backend-api/codex', 'api_mode': 'codex_responses'}
        else:
            yield from original(provider, key, url, model)
    monkeypatch.setattr(rp, '_ladder_rungs', ladder)
    if expected:
        assert rp.resolve_runtime_provider(requested='gemini', target_model=POLICY['model'])['provider'] == 'openai-codex'
    elif other != 'available':
        with pytest.raises(quota_fallback.GeminiPoolUnavailable):
            rp.resolve_runtime_provider(requested='gemini', target_model=POLICY['model'])
    assert seen == (['openai-codex'] if expected else [])
    assert cp.load_pool('gemini').entries()[0].last_status == cp.STATUS_DEAD


def test_daily_fallback_remains_eligible_after_non_daily_failure_in_same_turn(tmp_path, monkeypatch):
    import requests
    from agent import auxiliary_client as aux, credential_pool as cp
    from agent.chat_completion_helpers import try_activate_fallback
    from run_agent import AIAgent
    root = seed(tmp_path, monkeypatch)
    (root/'config.yaml').write_text(json.dumps({'quota_fallbacks': [POLICY]}), encoding='utf-8')
    monkeypatch.setattr(requests.sessions.Session, 'request', lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError('synthetic offline metadata')))
    monkeypatch.setattr(httpx.Client, 'send', lambda *a, **k: pytest.fail('unexpected network'))
    monkeypatch.setattr(aux, '_read_codex_access_token', lambda: 'dummy-luna')
    pool = cp.load_pool('gemini'); entry = pool.select()
    agent = AIAgent(provider='gemini', model=POLICY['model'], api_key=entry.runtime_api_key,
        base_url='https://generativelanguage.googleapis.com/v1beta', credential_pool=pool,
        skip_memory=True, skip_context_files=True, enabled_toolsets=[], quiet_mode=True)
    agent._cached_system_prompt = 'Immutable prefix.'
    try:
        assert try_activate_fallback(agent) is False
        assert try_activate_fallback(agent) is False
        for row in pool.entries():
            pool.mark_exhausted_and_rotate(status_code=429, credential_id=row.id,
                error_context={'message': 'GenerateRequestsPerDayPerProjectPerModel exceeded'})
        assert try_activate_fallback(agent) is True
        assert (agent.provider, agent.model) == ('openai-codex', POLICY['fallback']['model'])
        assert agent._cached_system_prompt == 'Immutable prefix.'
        assert try_activate_fallback(agent) is False
    finally:
        agent.client.close()


@pytest.mark.parametrize('task', ['title_generation', 'compression', 'approval', 'background_review', 'web_extract'])
@pytest.mark.parametrize('async_mode', [False, True])
@pytest.mark.parametrize('override', ['config', 'extra_reasoning', 'explicit', 'native'])
def test_aux_task_effort_reaches_native_request_without_explicit_override(tmp_path, monkeypatch, task, async_mode, override):
    from agent import auxiliary_client as aux
    root = seed(tmp_path, monkeypatch)
    config = {'provider': 'gemini', 'model': POLICY['model'], 'reasoning_effort': 'low'}
    if override != 'config':
        config['extra_body'] = {'reasoning': {'effort': 'high'}}
    (root/'config.yaml').write_text(json.dumps({'auxiliary': {task: config}}), encoding='utf-8')
    aux._client_cache.clear()
    seen = []
    def send(client, request, **kwargs):
        assert request.url.host == 'generativelanguage.googleapis.com'
        seen.append(json.loads(request.content))
        return httpx.Response(200, request=request, json={'candidates': [{'content': {'parts': [{'text': 'OK'}]}}]})
    monkeypatch.setattr(httpx.Client, 'send', send)
    kwargs = dict(task=task, messages=[{'role': 'user', 'content': 'Synthetic.'}], tools=[], max_tokens=64)
    if override == 'native':
        kwargs['extra_body'] = {'thinking_config': {'thinkingLevel': 'medium'}}
    if override == 'explicit':
        kwargs['reasoning_config'] = {'effort': 'medium'}
    response = asyncio.run(aux.async_call_llm(**kwargs)) if async_mode else aux.call_llm(**kwargs)
    assert response.choices[0].message.content == 'OK'
    expected = {'config': 'low', 'extra_reasoning': 'high', 'explicit': 'medium', 'native': 'medium'}[override]
    assert seen[0]['generationConfig']['thinkingConfig']['thinkingLevel'] == expected


@pytest.mark.parametrize('async_mode', [False, True])
def test_aux_rotates_after_503_then_daily_429_and_returns_response(tmp_path, monkeypatch, async_mode):
    from agent import auxiliary_client as aux, credential_pool as cp
    root = seed(tmp_path, monkeypatch)
    (root/'config.yaml').write_text(json.dumps({'auxiliary': {'title_generation': {
        'provider': 'gemini', 'model': POLICY['model'], 'reasoning_effort': 'low'}}}), encoding='utf-8')
    aux._client_cache.clear()
    monkeypatch.setattr(aux, '_TRANSIENT_RETRY_BACKOFF_BASE', 0)
    statuses = iter([503, 429, 200]); seen = []
    def send(client, request, **kwargs):
        assert request.url.host == 'generativelanguage.googleapis.com'
        status = next(statuses)
        seen.append((status, request.headers['x-goog-api-key']))
        body = {'candidates': [{'content': {'parts': [{'text': 'OK'}]}}]} if status == 200 else {
            'error': {'message': 'Service unavailable' if status == 503 else 'GenerateRequestsPerDayPerProjectPerModel exceeded'}}
        return httpx.Response(status, request=request, json=body)
    monkeypatch.setattr(httpx.Client, 'send', send)
    kwargs = dict(task='title_generation', messages=[{'role': 'user', 'content': 'Synthetic.'}], tools=[], max_tokens=64)
    response = asyncio.run(aux.async_call_llm(**kwargs)) if async_mode else aux.call_llm(**kwargs)
    assert response.choices[0].message.content == 'OK'
    assert [s for s, _ in seen] == [503, 429, 200]
    assert seen[0][1] == seen[1][1] and seen[1][1] != seen[2][1]
    assert any(e.last_error_reason == 'gemini_daily_quota' for e in cp.load_pool('gemini').entries())
