"""Real pool persistence and native wire contracts; dummy credentials only."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest


def seed(tmp_path, monkeypatch):
    from hermes_cli import auth
    root = tmp_path / 'root'
    root.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr('hermes_constants.get_default_hermes_root', lambda: root)
    monkeypatch.setenv('HERMES_HOME', str(root))
    monkeypatch.delenv('GOOGLE_API_KEY', raising=False)
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    rows = [dict(id=f'key{i}', label=f'dummy{i}', auth_type='api_key', priority=i,
                 source='manual', access_token=f'dummy-gemini-{i}') for i in range(2)]
    auth.write_credential_pool('gemini', rows)
    return root


@pytest.mark.parametrize('local_time', ['2026-03-08T00:01:00', '2026-11-01T00:01:00'])
def test_daily_quota_is_shared_across_profiles_processes_and_resets(tmp_path, monkeypatch, local_time):
    from agent import credential_pool as cp
    from agent.gemini_native_adapter import gemini_http_error
    from agent.agent_runtime_helpers import extract_api_error_context
    root = seed(tmp_path, monkeypatch)
    now = datetime.fromisoformat(local_time).replace(tzinfo=ZoneInfo('America/Los_Angeles'))
    clock = [now.timestamp()]
    monkeypatch.setattr(cp.time, 'time', lambda: clock[0])
    stale = cp.load_pool('gemini')
    profile = root / 'profiles' / 'forge'
    profile.mkdir(parents=True)
    monkeypatch.setenv('HERMES_HOME', str(profile))
    borrowed = cp.load_pool('gemini')
    error = gemini_http_error(httpx.Response(429, json={'error': {
        'status': 'RESOURCE_EXHAUSTED', 'message': 'Quota exceeded', 'details': [
            {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{
                'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier',
                'quotaDimensions': {'model': 'gemini-3.8-flash'},
                'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests'}]},
            {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '30s'}]}}))
    context = extract_api_error_context(error)
    assert borrowed.mark_exhausted_and_rotate(status_code=429, error_context=context, api_key_hint='dummy-gemini-0').id == 'key1'
    next_midnight = datetime.combine(now.date()+timedelta(days=1), datetime.min.time(), tzinfo=now.tzinfo).timestamp()
    assert borrowed.entries()[0].last_error_reset_at == next_midnight
    assert not (profile/'auth.json').exists(), 'borrower must not fork root keys/cooldowns'
    monkeypatch.setenv('HERMES_HOME', str(root))
    assert stale.select().id == 'key1', 'long-lived process must observe peer cooldown before selecting'
    stale.mark_exhausted_and_rotate(status_code=429, error_context=context, api_key_hint='dummy-gemini-1')
    assert cp.load_pool('gemini').select() is None
    clock[0] = next_midnight + 1
    assert cp.load_pool('gemini').select() is not None


def test_minute_auth_and_foreign_identity_do_not_become_daily_quota(tmp_path, monkeypatch):
    from agent import credential_pool as cp
    seed(tmp_path, monkeypatch)
    pool = cp.load_pool('gemini')
    pool.mark_exhausted_and_rotate(status_code=429, api_key_hint='foreign-key', error_context={'message': 'requests per day exceeded'})
    assert all(e.last_status != cp.STATUS_EXHAUSTED for e in pool.entries())
    pool.mark_exhausted_and_rotate(status_code=429, api_key_hint='dummy-gemini-0', error_context={'message': 'GenerateRequestsPerMinutePerProjectPerModel exceeded', 'reset_at': cp.time.time()+30})
    assert pool.entries()[0].last_error_reset_at < cp.time.time()+60
    pool.mark_exhausted_and_rotate(status_code=403, api_key_hint='dummy-gemini-1', error_context={'reason': 'API_KEY_INVALID', 'message': 'API key not valid'})
    assert pool.entries()[1].last_status == cp.STATUS_DEAD


def test_native_38_payload_preserves_tool_replay_and_supported_sampling():
    from agent.gemini_native_adapter import GeminiNativeClient, translate_gemini_response
    seen = []
    def wire(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': 'ok'}]}}]})
    first = translate_gemini_response({'candidates': [{'content': {'parts': [{
        'functionCall': {'id': 'issued-1', 'name': 'lookup', 'args': {'q': 'test'}},
        'thoughtSignature': 'opaque-signature'}]}}]}, 'gemini-3.8-flash')
    call = first.choices[0].message.tool_calls[0]
    tc = call.model_dump() if hasattr(call, 'model_dump') else vars(call)
    if not isinstance(tc['function'], dict): tc['function'] = vars(tc['function'])
    messages = [{'role': 'user', 'content': 'look up'}, {'role': 'assistant', 'content': '', 'tool_calls': [tc]},
                {'role': 'tool', 'tool_call_id': 'issued-1', 'name': 'lookup', 'content': 'found'}]
    with GeminiNativeClient(api_key='dummy', http_client=httpx.Client(transport=httpx.MockTransport(wire))) as client:
        client.chat.completions.create(model='gemini-3.8-flash', messages=messages, temperature=.7, top_p=.8, top_k=20,
            n=2, extra_body={'thinking_config': {'thinking_level': 'minimal', 'thinking_budget': 100}})
    cfg = seen[0]['generationConfig']
    assert cfg['temperature'] == .7 and cfg['topP'] == .8
    assert not {'topK','candidateCount'} & cfg.keys()
    assert cfg['thinkingConfig']['thinkingLevel'] == 'low'
    assert 'thinkingBudget' not in cfg['thinkingConfig']
    parts = [part for content in seen[0]['contents'] for part in content['parts']]
    assert next(p for p in parts if 'functionCall' in p)['thoughtSignature'] == 'opaque-signature'
    assert next(p for p in parts if 'functionCall' in p)['functionCall']['id'] == 'issued-1'
    assert next(p for p in parts if 'functionResponse' in p)['functionResponse']['id'] == 'issued-1'


def test_peer_process_and_cached_native_client_obey_binding_daily_reset(tmp_path, monkeypatch):
    import subprocess
    import sys
    import time
    from agent import credential_pool as cp
    from agent.gemini_native_adapter import GeminiNativeClient, GeminiAPIError
    root = seed(tmp_path, monkeypatch)
    pool = cp.load_pool('gemini')
    ready, go = tmp_path/'ready', tmp_path/'go'
    child = '''
import sys,time
from pathlib import Path
import hermes_constants
root,ready,go=map(Path,sys.argv[1:])
Path.home=classmethod(lambda cls: root.parent)
hermes_constants.get_default_hermes_root=lambda:root
from agent.credential_pool import load_pool
pool=load_pool('gemini')
ready.touch()
deadline=time.monotonic()+10
while not go.exists():
 if time.monotonic()>deadline:raise TimeoutError('parent never released probe')
 time.sleep(.01)
print(pool.select().id)
'''
    process = subprocess.Popen([sys.executable,'-c',child,str(root),str(ready),str(go)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic()+10
        while not ready.exists():
            assert process.poll() is None, process.communicate()[1]
            assert time.monotonic()<deadline
            time.sleep(.01)
        cp.load_pool('gemini').mark_exhausted_and_rotate(status_code=429, api_key_hint='dummy-gemini-0',
            error_context={'message':'GenerateRequestsPerDayPerProjectPerModel exceeded'})
        go.touch()
        out, err = process.communicate(timeout=10)
        assert process.returncode == 0, err
        assert out.strip() == 'key1'
    finally:
        if process.poll() is None:
            process.kill(); process.wait(timeout=5)
    def forbidden(request):
        pytest.fail('cooled cached credential reached HTTP')
    with GeminiNativeClient(api_key='dummy-gemini-0', credential_pool=pool,
            http_client=httpx.Client(transport=httpx.MockTransport(forbidden))) as client:
        with pytest.raises(GeminiAPIError):
            client.chat.completions.create(model='gemini-3.8-flash',messages=[{'role':'user','content':'synthetic'}])
    # A later racing minute response must not shorten a persisted daily quota.
    pool.mark_exhausted_and_rotate(status_code=429,api_key_hint='dummy-gemini-0',
        error_context={'message':'RPM exceeded','reset_at':time.time()+30})
    first = cp.load_pool('gemini').entries()[0]
    assert first.last_error_reason == 'gemini_daily_quota'
    assert first.last_error_reset_at > time.time()+30


def test_known_project_quota_cools_sibling_keys_but_not_other_provider(tmp_path,monkeypatch):
    from agent import credential_pool as cp
    from agent.gemini_native_adapter import gemini_http_error
    from agent.agent_runtime_helpers import extract_api_error_context
    from hermes_cli import auth
    seed(tmp_path,monkeypatch)
    rows=auth.read_credential_pool('gemini')
    for row in rows:row['quota_project']='projects/dummy-project'
    auth.write_credential_pool('gemini',rows)
    auth.write_credential_pool('other',[dict(rows[0],access_token='[REDACTED]')])
    error=gemini_http_error(httpx.Response(429,json={'error':{'message':'Requests per day exceeded', 'details':[
        {'@type':'type.googleapis.com/google.rpc.ErrorInfo','reason':'RATE_LIMIT_EXCEEDED',
         'metadata':{'consumer':'projects/dummy-project'}}]}}))
    pool=cp.load_pool('gemini')
    assert pool.mark_exhausted_and_rotate(status_code=429,api_key_hint='dummy-gemini-0',
        error_context=extract_api_error_context(error)) is None
    assert all(e.last_error_reason=='gemini_daily_quota' for e in cp.load_pool('gemini').entries())
    assert not auth.read_credential_pool('other')[0].get('last_status')
