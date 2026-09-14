"""Real cached gateway resolution followed by isolated native provider turns."""
import json
import threading
import time
from collections import OrderedDict
from types import SimpleNamespace

import httpx
import pytest
import requests

from tests.agent.test_gemini_outage_fallback import configure, POLICY


@pytest.fixture
def cached_gateway(tmp_path, monkeypatch):
    from agent import auxiliary_client as aux
    from gateway.run import GatewayRunner
    from gateway.run_turn_runner import TurnRunner
    from gateway.turn_context import TurnContext
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from run_agent import AIAgent
    root = configure(tmp_path, monkeypatch)
    monkeypatch.setattr('gateway.run._hermes_home', root)
    monkeypatch.setattr(requests.sessions.Session, 'request', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline metadata')))
    monkeypatch.setattr(aux, '_read_codex_access_token', lambda: 'dummy-luna')
    monkeypatch.setattr('agent.turn_api_error.compute_error_backoff', lambda *a, **k: 0)
    calls, state = [], {'failure': None}

    def wire(client, request, **kwargs):
        body = json.loads(request.content)
        calls.append(request.url.host)
        if request.url.host == 'generativelanguage.googleapis.com':
            status = state['failure']
            if status:
                message = 'GenerateRequestsPerDayPerProjectPerModel exceeded' if status == 429 else 'UNAVAILABLE high demand'
                return httpx.Response(status, request=request, json={'error': {'message': message}})
            value = {'candidates': [{'content': {'parts': [{'text': 'Primary.'}]}, 'finishReason': 'STOP'}]}
            return httpx.Response(200, request=request, content='data: ' + json.dumps(value) + '\n\n',
                                  headers={'Content-Type': 'text/event-stream'})
        assert request.url.host == 'chatgpt.com'
        assert body['model'] == POLICY['fallback']['model'] and body['reasoning']['effort'] == 'low'
        response = {'id': 'synthetic', 'object': 'response', 'status': 'completed', 'model': body['model'],
            'output': [{'id': 'msg_dummy', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                        'content': [{'type': 'output_text', 'text': 'Fallback.', 'annotations': []}]}],
            'usage': {'input_tokens': 2, 'output_tokens': 2, 'total_tokens': 4}}
        events = [{'type': 'response.output_text.delta', 'delta': 'Fallback.', 'output_index': 0,
                   'content_index': 0, 'item_id': 'msg_dummy'}, {'type': 'response.completed', 'response': response}]
        content = ''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e) + '\n\n' for e in events)
        return httpx.Response(200, request=request, content=content, headers={'Content-Type': 'text/event-stream'})

    monkeypatch.setattr(httpx.Client, 'send', wire)
    runtime = resolve_runtime_provider(requested='gemini', target_model=POLICY['model'])
    agent = AIAgent(provider=runtime['provider'], model=POLICY['model'], api_key=runtime['api_key'],
        base_url=runtime['base_url'], credential_pool=runtime['credential_pool'], skip_memory=True,
        skip_context_files=True, enabled_toolsets=[], quiet_mode=True, max_iterations=4,
        reasoning_config={'effort': 'low'})
    prefix = 'Immutable gateway Gemini prefix.'
    agent._cached_system_prompt = prefix
    first = agent.run_conversation('First turn.')
    assert first['final_response'] == 'Primary.' and calls == ['generativelanguage.googleapis.com']
    # Use the real cache lookup, initialization, disk refresh and application methods.
    runner = object.__new__(GatewayRunner)
    runner._fallback_model = None
    runner._session_db = None
    runner._agent_cache_lock = threading.Lock()
    ctx = TurnContext(user_config=json.loads((root / 'config.yaml').read_text()),
        enabled_toolsets=[], session_key='synthetic-session', session_id=agent.session_id,
        source=SimpleNamespace(user_id='dummy', user_id_alt=None))
    signature = runner._agent_config_signature(POLICY['model'], runtime, [], '',
        cache_keys=runner._extract_cache_busting_config(ctx.user_config), user_id='dummy', skip_context_files=False)
    runner._agent_cache = OrderedDict({ctx.session_key: (agent, signature, None, agent.session_id)})
    turn = TurnRunner(runner, ctx)
    history = first['messages']

    def reuse():
        found, reused = turn._resolve_turn_agent({'model': POLICY['model'], 'runtime': runtime}, 'discord', '', 4, {'effort': 'low'}, {})
        assert reused and found is agent
        assert found._cached_system_prompt == prefix
        return found

    def invoke():
        nonlocal history
        result = reuse().run_conversation('Next turn.', conversation_history=history)
        history = result['messages']
        assert agent._cached_system_prompt == prefix
        return result['final_response']

    try:
        yield SimpleNamespace(root=root, agent=agent, state=state, calls=calls, reuse=reuse, invoke=invoke)
    finally:
        agent.client.close()


@pytest.mark.parametrize('failure', [503, 429])
def test_cached_gateway_retains_policy_after_success_and_primary_restore(cached_gateway, monkeypatch, failure):
    from agent import credential_pool as cp
    from agent.agent_runtime_helpers import restore_primary_runtime
    g = cached_gateway
    auth_before = (g.root / 'auth.json').read_bytes()
    g.state['failure'] = failure
    assert g.invoke() == 'Fallback.'
    expected = g.agent._api_max_retries if failure == 503 else 2
    assert g.calls == ['generativelanguage.googleapis.com'] * (1 + expected) + ['chatgpt.com']
    assert (g.agent.provider, g.agent.model) == ('openai-codex', POLICY['fallback']['model'])
    if failure == 503:
        assert (g.root / 'auth.json').read_bytes() == auth_before
    else:
        from agent.gemini_quota import pool_daily_exhausted
        assert pool_daily_exhausted(cp.load_pool('gemini'))
    assert restore_primary_runtime(g.agent) is False
    cooled_auth = (g.root / 'auth.json').read_bytes()
    assert g.invoke() == 'Fallback.'
    assert g.calls[-2:] == ['chatgpt.com', 'chatgpt.com']
    assert (g.root / 'auth.json').read_bytes() == cooled_auth
    original_monotonic = time.monotonic
    monkeypatch.setattr(time, 'monotonic', lambda: original_monotonic() + 90000)
    if failure == 429:
        assert restore_primary_runtime(g.reuse()) is False
        assert g.invoke() == 'Fallback.'  # Short cooldown expiry cannot erase a daily limit.
        reset = cp.load_pool('gemini').next_available_at()
        monkeypatch.setattr(time, 'time', lambda: reset + 1)
    # Refresh runs while the current model is still Luna: policy must use the snapshot.
    assert g.reuse()._fallback_chain
    g.state['failure'] = None
    assert g.invoke() == 'Primary.'
    assert (g.agent.provider, g.agent.model) == ('gemini', POLICY['model'])
    assert g.agent._credential_pool.provider == 'gemini'
    g.state['failure'] = failure
    assert g.invoke() == 'Fallback.'


def test_cached_gateway_config_removal_and_explicit_chain_precedence(cached_gateway):
    g = cached_gateway
    assert g.reuse()._fallback_chain[0]['_daily_quota_only']
    config = json.loads((g.root / 'config.yaml').read_text())
    chosen = [{'provider': 'custom', 'model': 'user-selected'}]
    config['fallback_providers'] = chosen
    (g.root / 'config.yaml').write_text(json.dumps(config))
    assert g.reuse()._fallback_chain == chosen
    config.pop('fallback_providers')
    (g.root / 'config.yaml').write_text(json.dumps(config))
    assert g.reuse()._fallback_chain[0]['model'] == POLICY['fallback']['model']
    config.pop('quota_fallbacks')
    (g.root / 'config.yaml').write_text(json.dumps(config))
    assert g.reuse()._fallback_chain == []
    assert g.agent._fallback_model is None


def test_cached_policy_uses_one_config_read_and_keeps_last_good_on_parse_failure(cached_gateway, monkeypatch):
    from hermes_cli import config as config_module
    g = cached_gateway
    assert g.reuse()._fallback_chain
    previous = list(g.agent._fallback_chain)
    g.agent._unavailable_fallback_keys = {'keep'}
    (g.root / 'config.yaml').write_text('quota_fallbacks: [broken')
    assert g.reuse()._fallback_chain == previous
    assert g.agent._unavailable_fallback_keys == {'keep'}
    # A successful parsed snapshot is authoritative even if disk changes immediately after.
    config = {'quota_fallbacks': [dict(POLICY, outages=True)]}
    (g.root / 'config.yaml').write_text(json.dumps(config))
    read = config_module.read_user_config_raw
    reads = []

    def mutate_after_read(path):
        value = read(path)
        reads.append(path)
        (g.root / 'config.yaml').write_text('{}')
        return value

    monkeypatch.setattr(config_module, 'read_user_config_raw', mutate_after_read)
    assert g.reuse()._fallback_chain == previous
    assert len(reads) == 1
    monkeypatch.setattr(config_module, 'read_user_config_raw', read)
    assert g.reuse()._fallback_chain == []  # Valid removal is not a parse failure.
    assert not g.agent._unavailable_fallback_keys
