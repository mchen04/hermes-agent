"""Configured auxiliary routes use only their owning profile's credential pool."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import threading
import time
from types import SimpleNamespace

import pytest
import yaml


def test_native_compression_and_review_resolve_profile_pool_across_worker_threads(tmp_path, monkeypatch):
    from agent.auxiliary_client import get_text_auxiliary_client, shutdown_cached_clients
    from agent.background_review import _resolve_review_runtime
    from agent.context_compressor import ContextCompressor
    from agent.secret_scope import is_multiplex_active, set_multiplex_active
    from gateway.run import _profile_runtime_scope
    from hermes_cli.auth import AuthError, write_credential_pool

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, self.headers.get("x-goog-api-key"), body))
            payload = {
                "candidates": [{"content": {"role": "model", "parts": [{"text": "Keep the user's task."}]},
                                "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 5, "totalTokenCount": 10},
            }
            streaming = ":streamGenerateContent" in self.path
            response = (f"data: {json.dumps(payload)}\n\n" if streaming else json.dumps(payload)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if streaming else "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    real_getaddrinfo = socket.getaddrinfo

    def resolve_local_native_endpoint(host, *args, **kwargs):
        if host in ("generativelanguage.googleapis.com", b"generativelanguage.googleapis.com"):
            host = "127.0.0.1"
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve_local_native_endpoint)
    monkeypatch.setenv("NO_PROXY", "generativelanguage.googleapis.com,127.0.0.1")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    a = tmp_path / ".hermes" / "profiles" / "first"
    b = a.parent / "worker"
    monkeypatch.setenv("HERMES_HOME", str(a))
    base_url = f"http://generativelanguage.googleapis.com:{server.server_port}/v1beta"
    task_config = {"provider": "gemini", "model": "gemini-test", "base_url": base_url, "timeout": 5}
    for home in (a, b):
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.yaml").write_text(yaml.safe_dump({
            "model": {"provider": "openai-codex", "default": "main-model"},
            "auxiliary": {name: task_config for name in ("compression", "background_review")},
        }))
        (home / ".env").write_text(f"GEMINI_BASE_URL={base_url}\n")

    parent = SimpleNamespace(provider="openai-codex", model="main-model", _current_main_runtime=lambda: {
        "api_key": "parent-only-key", "base_url": "http://127.0.0.1:1", "api_mode": "codex_responses",
    })

    def provision(home, key):
        with _profile_runtime_scope(home, hydrate_secrets=False):
            write_credential_pool("gemini", [{
                "id": key, "source": "manual", "auth_type": "api_key", "access_token": key,
                "base_url": base_url, "priority": 0,
            }])

    def call_compression(key):
        review = _resolve_review_runtime(parent)
        assert review["routed"] is True
        assert (review["provider"], review["model"], review["api_key"]) == ("gemini", "gemini-test", key)
        client, model = get_text_auxiliary_client("compression")
        assert client.api_key == key
        client.close()
        assert model == "gemini-test"
        compressor = ContextCompressor(model=parent.model, provider=parent.provider,
                                       config_context_length=32768, quiet_mode=True)
        assert compressor._call_summary_llm("Summarize the user's task.", time.monotonic()) == "Keep the user's task."

    was_multiplex = is_multiplex_active()
    set_multiplex_active(True)
    try:
        provision(a, "a-test-key")
        with _profile_runtime_scope(b, hydrate_secrets=False):
            with pytest.raises(AuthError) as failure:
                _resolve_review_runtime(parent, {"provider": "gemini", "model": "gemini-test"})
            assert failure.value.provider == "gemini"
            assert get_text_auxiliary_client("compression") == (None, None)
        provision(b, "b-test-key")
        with ThreadPoolExecutor(max_workers=1) as executor:
            for home, key in ((a, "a-test-key"), (b, "b-test-key"), (a, "a-test-key")):
                with _profile_runtime_scope(home, hydrate_secrets=False):
                    executor.submit(copy_context().run, call_compression, key).result(timeout=15)
        assert [key for _, key, _ in requests] == ["a-test-key", "b-test-key", "a-test-key"]
        assert all("/models/gemini-test:" in path for path, _, _ in requests)
        assert all("contents" in body for _, _, body in requests)
    finally:
        shutdown_cached_clients()
        set_multiplex_active(was_multiplex)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
