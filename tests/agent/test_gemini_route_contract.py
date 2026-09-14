"""Opt-in quota fallback through runtime, auxiliary and agent resolution."""
from types import SimpleNamespace
import json

import pytest

from pathlib import Path

def seed(tmp_path, monkeypatch):
    from hermes_cli import auth
    root = tmp_path / 'root'
    root.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr('hermes_constants.get_default_hermes_root', lambda: root)
    monkeypatch.setenv('HERMES_HOME', str(root))
    monkeypatch.delenv('GOOGLE_API_KEY', raising=False)
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    auth.write_credential_pool('gemini', [dict(id=f'key{i}', label=f'dummy{i}', auth_type='api_key', priority=i,
        source='manual', access_token=f'dummy-gemini-{i}') for i in range(2)])
    return root

POLICY = {'provider': 'gemini', 'model': 'gemini-3.8-flash', 'fallback': {
    'provider': 'openai-codex', 'model': 'gpt-5.6-luna', 'reasoning_effort': 'low'}}


def setup_route(tmp_path, monkeypatch):
    from agent import credential_pool as cp
    root = seed(tmp_path, monkeypatch)
    (root/'config.yaml').write_text(json.dumps({'model': {'provider': 'openai-codex', 'default': 'gpt-6-astra'},
        'quota_fallbacks': [POLICY]}))
    pool = cp.load_pool('gemini')
    for e in pool.entries():
        pool.mark_exhausted_and_rotate(status_code=429, credential_id=e.id,
            error_context={'reason': 'gemini_daily_quota', 'message': 'Daily request quota exceeded'})
    return root, pool


def test_runtime_quota_fallback_is_model_specific_and_keeps_credentials_separate(tmp_path, monkeypatch):
    from hermes_cli import runtime_provider as rp
    root, pool = setup_route(tmp_path, monkeypatch)
    original = rp._ladder_rungs
    calls = []
    def ladder(provider, key, url, model):
        if provider == 'openai-codex':
            calls.append((key, url, model))
            yield {'provider': provider, 'requested_provider': provider, 'api_mode': 'codex_responses',
                   'api_key': 'dummy-luna', 'base_url': 'https://chatgpt.com/backend-api/codex'}
        else:
            yield from original(provider, key, url, model)
    monkeypatch.setattr(rp, '_ladder_rungs', ladder)
    runtime = rp.resolve_runtime_provider(requested='gemini', target_model='gemini-3.8-flash')
    assert runtime['provider'] == 'openai-codex'
    assert runtime['model'] == 'gpt-5.6-luna'
    assert calls == [(None, None, 'gpt-5.6-luna')]
    assert runtime['api_key'] == 'dummy-luna'
    with pytest.raises(Exception):
        rp.resolve_runtime_provider(requested='gemini', target_model='gemini-3.7-flash')
    assert len(calls) == 1
    explicit = rp.resolve_runtime_provider(requested='gemini', target_model='gemini-3.8-flash', explicit_api_key='dummy-explicit')
    assert explicit['provider'] == 'gemini' and explicit['api_key'] == 'dummy-explicit'
    assert len(calls) == 1
    # Pool exhaustion can occur between auxiliary cache preflight and runtime resolution.
    from agent import auxiliary_client as aux
    from agent.quota_fallback import resolve_gemini_client
    config = json.loads((root/'config.yaml').read_text())
    config['quota_fallbacks'][0]['fallback'].update(api_key='dummy-fallback', base_url='https://fallback.invalid',
                                                  api_mode='codex_responses')
    (root/'config.yaml').write_text(json.dumps(config))
    routed = []
    def resolve(provider, model, **kwargs):
        routed.append((provider, model, kwargs))
        return SimpleNamespace(), model
    monkeypatch.setattr(aux, 'resolve_provider_client', resolve)
    client, model = resolve_gemini_client(SimpleNamespace(task=None, explicit_api_key=None, explicit_base_url=None,
        model='gemini-3.8-flash', async_mode=False, raw_codex=False))
    assert routed[0][:2] == ('openai-codex', 'gpt-5.6-luna')
    assert routed[0][2]['explicit_api_key'] == 'dummy-fallback'
    assert routed[0][2]['explicit_base_url'] == 'https://fallback.invalid'
    assert client._hermes_quota_fallback['reasoning_effort'] == 'low'


def test_agent_fallback_binds_only_matching_route_and_respects_explicit_chain(tmp_path, monkeypatch):
    from agent.agent_init import _init_fallback_chain
    setup_route(tmp_path, monkeypatch)
    for provider, model, fallback, expected in [
        ('gemini', 'gemini-3.8-flash', None, 'gpt-5.6-luna'),
        ('openai-codex', 'gpt-6-astra', None, None),
        ('gemini', 'gemini-3.8-flash', [{'provider': 'custom', 'model': 'explicit'}], 'explicit')]:
        agent = SimpleNamespace(provider=provider, model=model, base_url='https://generativelanguage.googleapis.com/v1beta',
            _credential_pool=None, api_key='dummy', quiet_mode=True)
        _init_fallback_chain(agent, fallback)
        assert (agent._fallback_chain[0]['model'] if agent._fallback_chain else None) == expected


@pytest.mark.parametrize('task', [None, 'compression', 'title_generation'])
@pytest.mark.parametrize('async_mode', [False, True])
def test_auxiliary_rotates_daily_keys_then_luna_and_reselects_gemini(tmp_path, monkeypatch, task, async_mode):
    import httpx
    from agent import auxiliary_client as aux, credential_pool as cp
    root = seed(tmp_path, monkeypatch)
    (root/'config.yaml').write_text(json.dumps({'model': {'provider': 'openai-codex', 'default': 'gpt-6-astra'},
        'quota_fallbacks': [POLICY], 'auxiliary': {task or 'unused': {
            'provider': 'gemini', 'model': 'gemini-3.8-flash', 'reasoning_effort': 'low'}}}))
    aux._client_cache.clear()
    seen = []
    daily = [True]
    def send(client, request, **kwargs):
        assert request.url.host == 'generativelanguage.googleapis.com'
        seen.append(('gemini', request.headers['x-goog-api-key'], json.loads(request.content)))
        if daily[0]:
            return httpx.Response(429, request=request, json={'error': {'status': 'RESOURCE_EXHAUSTED',
                'message': 'GenerateRequestsPerDayPerProjectPerModel exceeded'}})
        return httpx.Response(200, request=request, json={'candidates': [{'content': {'parts': [{'text': 'primary'}]}}],
            'usageMetadata': {'promptTokenCount': 2, 'candidatesTokenCount': 1, 'totalTokenCount': 3}})
    monkeypatch.setattr(httpx.Client, 'send', send)
    def luna_create(**kwargs):
        seen.append(('luna', 'dummy-luna', kwargs))
        assert kwargs['model'] == 'gpt-5.6-luna'
        assert not kwargs.get('tools')
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='fallback', tool_calls=[]), finish_reason='stop')],
            usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3))
    luna = SimpleNamespace(api_key='dummy-luna', base_url='https://chatgpt.com/backend-api/codex',
        chat=SimpleNamespace(completions=SimpleNamespace(create=luna_create)))
    if async_mode:
        async def async_luna_create(**kwargs):
            return luna_create(**kwargs)
        luna.chat.completions.create = async_luna_create
        luna.HERMES_SKIP_ASYNC_WRAP = True
    monkeypatch.setattr(aux, '_build_codex_client', lambda model: (luna, model))
    # The Codex credential authority is outside this test; Gemini uses its real dummy auth store.
    original_pool = aux.load_pool
    monkeypatch.setattr(aux, 'load_pool', lambda provider: cp.CredentialPool(provider, []) if provider == 'openai-codex' else original_pool(provider))
    route = {}
    args = dict(task=task, provider='gemini', model='gemini-3.8-flash', messages=[{'role':'user','content':'synthetic'}],
        tools=[], max_tokens=100, timeout=5, reasoning_config={'effort':'low'}, route_info=route)
    def invoke():
        if async_mode:
            import asyncio
            return asyncio.run(aux.async_call_llm(**args))
        return aux.call_llm(**args)
    response = invoke()
    assert response.choices[0].message.content == 'fallback'
    assert [(r[0],r[1]) for r in seen] == [('gemini','dummy-gemini-0'),('gemini','dummy-gemini-1'),('luna','dummy-luna')]
    assert seen[0][2]['generationConfig']['thinkingConfig']['thinkingLevel'] == 'low'
    assert seen[2][2]['extra_body']['reasoning']['effort'] == 'low'
    assert route == {'provider':'openai-codex','model':'gpt-5.6-luna'}
    invoke()
    assert [r[0] for r in seen] == ['gemini','gemini','luna','luna']
    reset = cp.load_pool('gemini').next_available_at()
    monkeypatch.setattr(cp.time, 'time', lambda: reset+1)
    daily[0] = False
    assert invoke().choices[0].message.content == 'primary'
    assert seen[-1][0] == 'gemini'
    assert route['provider'] == 'gemini'


def test_channel_and_cron_resolve_provider_model_together(tmp_path, monkeypatch):
    from hermes_cli import runtime_provider as rp
    from gateway import run as gateway
    from gateway.config import Platform, ChannelOverride, PlatformConfig
    from gateway.session import SessionSource
    from cron.scheduler import _resolve_job_runtime, _CronJobConfig
    root, pool = setup_route(tmp_path, monkeypatch)
    original = rp._ladder_rungs
    def ladder(provider, key, url, model):
        if provider == 'openai-codex':
            yield {'provider': provider, 'requested_provider': provider, 'api_mode': 'codex_responses',
                   'api_key': 'dummy-luna', 'base_url': 'https://chatgpt.com/backend-api/codex'}
        else:
            yield from original(provider, key, url, model)
    monkeypatch.setattr(rp, '_ladder_rungs', ladder)
    config = json.loads((root/'config.yaml').read_text())
    runner = SimpleNamespace(config=SimpleNamespace(platforms={Platform.DISCORD: PlatformConfig(channel_overrides={
        'channel':ChannelOverride(provider='gemini',model='gemini-3.8-flash')})}),
        _resolve_session_key_or_none=lambda *a: None, _sessions_map=lambda: {},
        _session_state=lambda *a: SimpleNamespace(conversation=SimpleNamespace(last_resolved_model=None)))
    source = SessionSource(platform=Platform.DISCORD, chat_id='channel')
    model, runtime = gateway.GatewayRunner._resolve_session_agent_runtime(runner, source=source, user_config=config)
    assert (runtime['provider'], model) == ('openai-codex', 'gpt-5.6-luna')
    jc = _CronJobConfig(cfg=config,model='gemini-3.8-flash',model_cfg=config['model'],cron_default_provider='')
    runtime, model = _resolve_job_runtime({'id':'dummy-job','provider':'gemini'}, 'dummy-job', jc)
    assert (runtime['provider'], model) == ('openai-codex','gpt-5.6-luna')
    # An unrelated channel still uses the unchanged Astra primary.
    source = SessionSource(platform=Platform.DISCORD,chat_id='unrelated')
    model, runtime = gateway.GatewayRunner._resolve_session_agent_runtime(runner, source=source, user_config=config)
    assert model == 'gpt-6-astra'


@pytest.mark.parametrize('status,message', [(429,'GenerateRequestsPerMinutePerProjectPerModel exceeded'),
    (503,'Service unavailable'), (403,'API key not valid')])
def test_transient_and_auth_failures_never_authorize_quota_fallback(tmp_path, monkeypatch, status, message):
    import httpx
    from agent import auxiliary_client as aux
    root = seed(tmp_path, monkeypatch)
    (root/'config.yaml').write_text(json.dumps({'quota_fallbacks':[POLICY]}))
    aux._client_cache.clear()
    calls = []
    def send(client, request, **kwargs):
        calls.append(request.url.host)
        assert request.url.host == 'generativelanguage.googleapis.com'
        return httpx.Response(status,request=request,json={'error':{'message':message}})
    monkeypatch.setattr(httpx.Client,'send',send)
    monkeypatch.setattr(aux,'_build_codex_client',lambda model: pytest.fail('non-daily error selected Luna'))
    with pytest.raises(Exception):
        aux.call_llm(provider='gemini',model='gemini-3.8-flash',messages=[{'role':'user','content':'synthetic'}],timeout=.1)
    assert calls and len(calls)<=4


def test_real_agent_turn_falls_back_without_rewriting_conversation_prefix(tmp_path, monkeypatch):
    import httpx
    import requests
    from agent import auxiliary_client as aux, credential_pool as cp
    from run_agent import AIAgent
    root = seed(tmp_path,monkeypatch)
    (root/'config.yaml').write_text(json.dumps({'quota_fallbacks':[POLICY], 'agent':{'reasoning_overrides':{'gpt-5.6-luna':'low'}}}))
    monkeypatch.setattr(requests.sessions.Session,'request',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('No external network in contract')))
    monkeypatch.setattr(aux,'_read_codex_access_token',lambda:'dummy-luna')
    seen=[]
    def send(client,request,**kwargs):
        body=json.loads(request.content)
        seen.append((request.url.host,body))
        if request.url.host=='generativelanguage.googleapis.com':
            return httpx.Response(429,request=request,json={'error':{'message':'GenerateRequestsPerDayPerProjectPerModel exceeded'}})
        assert request.url.host=='chatgpt.com'
        assert body['model']=='gpt-5.6-luna'
        response={'id':'resp_dummy','object':'response','status':'completed','model':'gpt-5.6-luna',
          'output':[{'id':'msg_dummy','type':'message','role':'assistant','status':'completed',
                     'content':[{'type':'output_text','text':'Synthetic success.','annotations':[]}]}],
          'usage':{'input_tokens':2,'output_tokens':2,'total_tokens':4}}
        events=[{'type':'response.output_text.delta','delta':'Synthetic success.','output_index':0,'content_index':0,'item_id':'msg_dummy'},
                {'type':'response.completed','response':response}]
        content=''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events)
        return httpx.Response(200,request=request,content=content,headers={'Content-Type':'text/event-stream'})
    monkeypatch.setattr(httpx.Client,'send',send)
    pool=cp.load_pool('gemini');entry=pool.select()
    agent=AIAgent(provider='gemini',model='gemini-3.8-flash',api_key=entry.runtime_api_key,
        base_url='https://generativelanguage.googleapis.com/v1beta',credential_pool=pool,
        skip_memory=True,skip_context_files=True,enabled_toolsets=[],quiet_mode=True,max_iterations=4,
        reasoning_config={'effort':'low'})
    prefix='Model: gemini-3.8-flash\nProvider: gemini\nImmutable synthetic prefix.'
    agent._cached_system_prompt=prefix
    try:
        result=agent.run_conversation('Return a synthetic greeting.')
        assert result['final_response']=='Synthetic success.'
        assert agent.provider=='openai-codex' and agent.model=='gpt-5.6-luna'
        assert agent._cached_system_prompt==prefix
        assert [host for host,_ in seen].count('generativelanguage.googleapis.com')==2
        assert seen[-1][0]=='chatgpt.com'
        from agent.agent_runtime_helpers import restore_primary_runtime
        assert restore_primary_runtime(agent) is False
        reset=cp.load_pool('gemini').next_available_at()
        old_monotonic=cp.time.monotonic()
        monkeypatch.setattr(cp.time,'time',lambda:reset+1)
        monkeypatch.setattr(cp.time,'monotonic',lambda:old_monotonic+90000)
        assert restore_primary_runtime(agent) is True
        assert (agent.provider,agent.model)==('gemini','gemini-3.8-flash')
        assert agent._cached_system_prompt==prefix
    finally:
        agent.client.close()


def test_explicit_auxiliary_fallback_wins_over_implicit_daily_policy(tmp_path,monkeypatch):
    from agent import auxiliary_client as aux
    root,pool=setup_route(tmp_path,monkeypatch)
    cfg=json.loads((root/'config.yaml').read_text())
    cfg['auxiliary']={'title_generation':{'provider':'gemini','model':'gemini-3.8-flash',
        'fallback_chain':[{'provider':'custom','model':'explicit-model','base_url':'http://localhost:1234/v1','api_key':'dummy-explicit'}]}}
    (root/'config.yaml').write_text(json.dumps(cfg))
    aux._client_cache.clear()
    monkeypatch.setattr(aux,'_build_codex_client',lambda model:pytest.fail('implicit Luna overrode explicit task fallback'))
    calls=[]
    import httpx
    def send(client,request,**kwargs):
        calls.append(json.loads(request.content))
        assert request.url.host=='localhost'
        assert request.headers['authorization']=='Bearer dummy-explicit'
        return httpx.Response(200,request=request,json={'id':'dummy','choices':[{'index':0,'message':{'role':'assistant','content':'explicit'},'finish_reason':'stop'}]})
    monkeypatch.setattr(httpx.Client,'send',send)
    result=aux.call_llm(task='title_generation',messages=[{'role':'user','content':'synthetic'}],timeout=5)
    assert result.choices[0].message.content=='explicit' and calls[0]['model']=='explicit-model'
