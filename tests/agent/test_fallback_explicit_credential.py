"""Explicit credentials remain isolated from same-provider canonical pools on restore."""
import json

import httpx
import requests
import pytest

from tests.agent.test_gemini_outage_fallback import configure, POLICY


@pytest.mark.parametrize('fallback_has_pool', [False, True])
def test_explicit_primary_key_survives_fallback_and_restore_with_canonical_pool(tmp_path, monkeypatch, fallback_has_pool):
    from agent import auxiliary_client as aux, credential_pool as cp
    from agent.agent_runtime_helpers import restore_primary_runtime, _build_primary_runtime_snapshot
    from run_agent import AIAgent
    root = configure(tmp_path, monkeypatch)
    if fallback_has_pool:
        from hermes_cli import auth
        auth.write_credential_pool('openai-codex', [dict(id='codex', label='dummy', auth_type='oauth',
            source='manual', access_token='dummy-luna', base_url='https://chatgpt.com/backend-api/codex')])
    original_auth = (root / 'auth.json').read_bytes()
    monkeypatch.setattr(requests.sessions.Session, 'request', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline metadata')))
    monkeypatch.setattr(aux, '_read_codex_access_token', lambda: 'dummy-luna')
    keys = []

    def wire(client, request, **kwargs):
        assert request.url.host == 'generativelanguage.googleapis.com'
        keys.append(request.headers.get('x-goog-api-key'))
        value = {'candidates': [{'content': {'parts': [{'text': 'Explicit.'}]}, 'finishReason': 'STOP'}]}
        return httpx.Response(200, request=request, content='data: ' + json.dumps(value) + '\n\n',
                              headers={'Content-Type': 'text/event-stream'})

    monkeypatch.setattr(httpx.Client, 'send', wire)
    assert cp.load_pool('gemini').has_available()
    agent = AIAgent(provider='gemini', model=POLICY['model'], api_key='dummy-explicit',
        base_url='https://generativelanguage.googleapis.com/v1beta', credential_pool=None,
        fallback_model=[POLICY['fallback']], skip_memory=True, skip_context_files=True,
        enabled_toolsets=[], quiet_mode=True, max_iterations=3)
    try:
        assert agent.run_conversation('First.')['final_response'] == 'Explicit.'
        for switched_snapshot in [False, True]:
            if switched_snapshot:
                agent._primary_runtime = _build_primary_runtime_snapshot(agent, agent.api_mode)
            assert agent._try_activate_fallback()
            assert agent.provider == 'openai-codex'
            assert (agent._credential_pool is not None) == fallback_has_pool
            assert restore_primary_runtime(agent)
            assert agent.api_key == 'dummy-explicit'
            assert agent._credential_pool is None
            assert agent.run_conversation('Again.')['final_response'] == 'Explicit.'
        assert keys == ['dummy-explicit'] * 3
        assert (root / 'auth.json').read_bytes() == original_auth
    finally:
        agent.client.close()
