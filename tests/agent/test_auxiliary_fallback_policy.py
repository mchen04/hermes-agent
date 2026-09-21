"""Strict auxiliary fallback preserves the configured routes and failure conditions."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import asyncio
import json
from pathlib import Path
import socket
import threading
import time

import pytest
import yaml
from openai import InternalServerError


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("failure,backup_failure,allowed", [
    (503, False, True),
    ("daily", False, True),
    (401, False, False),
    (429, False, False),
    (404, False, False),
    (503, True, False),
])
def test_only_configured_backup_after_allowed_failure(tmp_path, monkeypatch, failure, backup_failure, allowed, async_mode):
    from agent import auxiliary_client as aux
    from agent.gemini_native_adapter import GeminiAPIError
    from hermes_cli.auth import write_credential_pool
    from gateway.run import _profile_runtime_scope

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            primary = "/models/" in self.path
            lane = "primary" if primary else ("backup" if self.path.startswith("/backup/") else "main")
            requests.append((lane, self.headers.get("x-goog-api-key")))
            status = (429 if failure == "daily" else failure) if primary else (503 if backup_failure else 200)
            if status != 200:
                message = "daily quota exhausted; requests per day limit" if failure == "daily" else "upstream unavailable"
                body = json.dumps({"error": {"message": message, "code": status}}).encode()
            else:
                body = json.dumps({
                    "id": "test", "object": "chat.completion", "created": 1, "model": payload["model"],
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    resolve = socket.getaddrinfo

    def local_address(host, *args, **kwargs):
        if host in ("generativelanguage.googleapis.com", b"generativelanguage.googleapis.com"):
            host = "127.0.0.1"
        return resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", local_address)
    monkeypatch.setenv("NO_PROXY", "generativelanguage.googleapis.com,127.0.0.1")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(aux, "_TRANSIENT_RETRY_BACKOFF_BASE", 0)
    endpoint = f"http://generativelanguage.googleapis.com:{server.server_port}/v1beta"
    main = {"provider": "custom", "model": "main-model", "default": "main-model",
            "base_url": f"http://127.0.0.1:{server.server_port}/main/v1", "api_key": "main-key"}
    task = {"provider": "gemini", "model": "gemini-test", "base_url": endpoint,
            "fallback_on": ["daily_quota", "server_unavailable"], "fallback_chain": [{
                "provider": "custom", "model": "budget-model", "api_key": "backup-key",
                "base_url": f"http://127.0.0.1:{server.server_port}/backup/v1",
            }]}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "model": main, "auxiliary": {"approval": task, "transient_retries": 1},
    }))
    write_credential_pool("gemini", [
        {"id": key, "source": "manual", "auth_type": "api_key", "access_token": key,
         "base_url": endpoint, "priority": priority}
        for priority, key in enumerate(("first-key", "second-key"))
    ])
    aux.shutdown_cached_clients()
    aux._reset_aux_unhealthy_cache()

    def invoke():
        route = {}
        args = dict(task="approval", main_runtime=main,
                    messages=[{"role": "user", "content": "Reply OK."}], timeout=2, route_info=route)
        response = asyncio.run(aux.async_call_llm(**args)) if async_mode else aux.call_llm(**args)
        assert route == {"provider": "custom", "model": "budget-model"}
        return response

    try:
        if allowed:
            response = invoke()
            assert response.choices[0].message.content == "OK"
        else:
            with pytest.raises(InternalServerError if backup_failure else GeminiAPIError) as error:
                invoke()
            assert error.value.status_code == failure
        assert any(lane == "primary" for lane, _ in requests)
        assert not any(lane == "main" for lane, _ in requests)
        assert sum(lane == "backup" for lane, _ in requests) == int(allowed or backup_failure)
        if failure == 503:
            assert [key for lane, key in requests if lane == "primary"] == ["first-key", "first-key"]
        if failure == "daily":
            assert [key for lane, key in requests if lane == "primary"] == ["first-key", "second-key"]
        if allowed:
            # A later call still uses the authorized backup, without retrying an exhausted
            # pool or an endpoint whose required 60-second outage cooldown has not elapsed.
            aux.shutdown_cached_clients()
            assert invoke().choices[0].message.content == "OK"
            assert sum(lane == "primary" for lane, _ in requests) == 2
            assert sum(lane == "backup" for lane, _ in requests) == 2
        if allowed and failure == 503:
            other = tmp_path / ".hermes" / "profiles" / "other"
            other.mkdir(parents=True)
            (other / "config.yaml").write_text((tmp_path / "config.yaml").read_text())
            with _profile_runtime_scope(other, hydrate_secrets=False):
                write_credential_pool("gemini", [{
                    "id": "other-key", "source": "manual", "auth_type": "api_key",
                    "access_token": "other-key", "base_url": endpoint, "priority": 0,
                }])
                assert invoke().choices[0].message.content == "OK"
            assert [key for lane, key in requests if lane == "primary"] == [
                "first-key", "first-key", "other-key", "other-key"]
            assert invoke().choices[0].message.content == "OK"
            assert sum(lane == "primary" for lane, _ in requests) == 4
            after_cooldown = time.time() + 61
            monkeypatch.setattr(time, "time", lambda: after_cooldown)
            assert invoke().choices[0].message.content == "OK"
            assert [key for lane, key in requests if lane == "primary"][-2:] == ["first-key", "first-key"]
            assert sum(lane == "primary" for lane, _ in requests) == 6
        assert not any(lane == "main" for lane, _ in requests)
    finally:
        aux.shutdown_cached_clients()
        aux._reset_aux_unhealthy_cache()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_strict_compression_policy_has_no_auth_stall_or_main_model_escape(tmp_path, monkeypatch):
    from agent.auxiliary_client import _try_configured_fallback_for_unavailable_client
    from agent.context_compressor import ContextCompressor
    from agent.conversation_compression import resolve_compression_fallback_route

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"auxiliary": {"compression": {
        "provider": "gemini", "model": "summary-model",
        "fallback_on": ["daily_quota", "server_unavailable"],
        "fallback_chain": [{"provider": "custom", "model": "budget-model", "api_key": "test-key",
                            "base_url": "http://127.0.0.1:1/v1"}],
    }}}))
    assert _try_configured_fallback_for_unavailable_client("compression", "gemini") == (None, None, "")
    assert resolve_compression_fallback_route() is None
    compressor = ContextCompressor(model="main-model", summary_model_override="summary-model",
                                   config_context_length=32768, quiet_mode=True)

    def forbidden_main_retry(*_args, **_kwargs):
        pytest.fail("strict fallback must not retry compression on the main model")

    monkeypatch.setattr(compressor, "_generate_summary", forbidden_main_retry)
    assert compressor._on_summary_failure(RuntimeError("invalid API key"), [], None, "") is None
    assert compressor.summary_model == "summary-model"
