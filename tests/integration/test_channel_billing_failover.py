"""#general billing failover through the real gateway route + native agent loop (controlled).

Tier (stated honestly): IN-PROCESS. The gateway's own config loader (``load_gateway_config``),
``GatewayRunner._resolve_session_agent_runtime`` / ``_resolve_turn_agent_config`` /
``_fallback_chain_for_source`` and the native ``AIAgent`` turn loop (real Anthropic and OpenAI SDK
clients, real error classifier, real credential pool and auth.json persistence) run as shipped.
Two vendor boundaries are loopback fakes reached at their PRODUCTION hosts: an ``HTTPS_PROXY`` with
a throwaway test CA terminates ``api.anthropic.com`` (``AnthropicMessagesServer``, branching on the
``x-api-key`` it receives) and ``chatgpt.com`` (``FakeResponsesServer``, the Codex backend), so the
first-party-host gates for fast-mode/tier params run as in production; every other CONNECT is refused
and recorded. ``HOME``/``HERMES_HOME`` are sandboxed and borrowed CLI logins are off, so no real
credential, network or Discord API is touched. The Discord adapter itself is not exercised.

Scenario (the production #general shape): channel override ``anthropic`` / ``claude-opus-5-5`` /
``service_tier: ultrafast`` with a channel-local ``fallback_providers: [openai-codex gpt-6.1-sol]``,
``credential_pool_policies.anthropic`` = API keys only + 24 h billing re-check, two pool keys.
"""

from __future__ import annotations

import base64
import json
import socket
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.fakes.providers.anthropic_messages import AnthropicMessagesServer, ApiError, Reply, Text
from tests.fakes.providers.oauth_token_server import TLSInterceptProxy, make_test_ca
from tests.fakes.providers.openai_responses import FakeResponsesServer, Message, Turn
from tests.fakes.providers.openai_responses import _handler_for as _responses_handler_for

VENDOR_HOSTS = {"api.anthropic.com": "anthropic", "chatgpt.com": "codex"}


class VendorProxy(TLSInterceptProxy):
    """``TLSInterceptProxy`` serving each allowlisted vendor host with its own fake's handler."""

    def __init__(self, ca, handlers):
        self._handlers = {h.lower(): cls for h, cls in handlers.items()}
        super().__init__(None, ca, self._handlers)  # type: ignore[arg-type]

    def _serve(self, conn, addr):
        import ssl
        try:
            conn.settimeout(30)
            head = b""
            while b"\r\n\r\n" not in head and len(head) < 65536:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                head += chunk
            request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
            method, target, _ = (request_line.split(" ") + ["", "", ""])[:3]
            host = target.rsplit(":", 1)[0].lower()
            if method != "CONNECT" or host not in self._handlers:
                self.refused.append(request_line)
                conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                return
            self.connects.append(host)
            conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            tls = self._ctx.wrap_socket(conn, server_side=True)
            self._handlers[host](tls, addr, None)
        except (OSError, ssl.SSLError):
            return
        finally:
            try:
                conn.close()
            except OSError:
                pass

GENERAL = "1544027215191216150"
OTHER = "1544444248869441627"
KEY1 = "sk-ant-api03-e2e-key-one"
KEY2 = "sk-ant-api03-e2e-key-two"
CREDIT_MSG = ("Your credit balance is too low to access the Anthropic API. "
              "Please go to Plans & Billing to upgrade or purchase credits.")


def _jwt(exp: float) -> str:
    def seg(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    payload = {"exp": int(exp), "sub": "user-e2e", "https://api.openai.com/auth": {"chatgpt_account_id": "acct-e2e"}}
    return f"{seg({'alg': 'none'})}.{seg(payload)}.sig"


class Clock:
    """Controlled clock: shifts ``time.time`` and ``time.monotonic`` for every module at once."""

    def __init__(self, monkeypatch):
        self.offset = 0.0
        real_time, real_mono = time.time, time.monotonic
        monkeypatch.setattr(time, "time", lambda: real_time() + self.offset)
        monkeypatch.setattr(time, "monotonic", lambda: real_mono() + self.offset)


def _write_home(root: Path, anthropic_url) -> Path:
    home = root / "hermes-home"
    home.mkdir(parents=True)
    config = f"""
model:
  provider: openai-codex
  default: gpt-6-astra
agent:
  reasoning_effort: high
  reasoning_overrides:
    gpt-6.1-sol: high
    claude-opus-5-5: medium
auth:
  adopt_external_logins: false
credential_pool_policies:
  anthropic:
    auth_types: [api_key]
    billing_recheck_hours: 24
fallback_providers: []
memory:
  memory_enabled: false
  user_profile_enabled: false
discord:
  channel_overrides:
    "{GENERAL}":
      model: claude-opus-5-5
      provider: anthropic
      service_tier: ultrafast
      fallback_providers:
        - provider: openai-codex
          model: gpt-6.1-sol
    "{OTHER}":
      model: gpt-6-luna
      provider: openai-codex
"""
    (home / "config.yaml").write_text(config, encoding="utf-8")
    tokens = {"access_token": _jwt(time.time() + 30 * 86400), "refresh_token": "rt-e2e"}
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    store = {
        "version": 1,
        "providers": {"openai-codex": {"tokens": dict(tokens), "last_refresh": now_iso, "auth_mode": "chatgpt"}},
        "credential_pool": {
            "anthropic": [
                {"id": "ak1", "label": "anthropic-free-200-1", "auth_type": "api_key", "priority": 0,
                 "source": "manual", "access_token": KEY1},
                {"id": "ak2", "label": "anthropic-free-200-2", "auth_type": "api_key", "priority": 1,
                 "source": "manual", "access_token": KEY2},
            ],
            "openai-codex": [
                {"id": "cx1", "label": "device_code", "auth_type": "oauth", "priority": 0, "source": "device_code",
                 **tokens, "last_refresh": now_iso},
            ],
        },
    }
    (home / "auth.json").write_text(json.dumps(store), encoding="utf-8")
    (home / "auth.json").chmod(0o600)
    return home


@contextmanager
def _sandbox(tmp_path, monkeypatch, anthropic_responder, codex_text="FALLBACK-CODEX"):
    with AnthropicMessagesServer(anthropic_responder) as ant, \
            FakeResponsesServer(lambda _r: Turn([Message(codex_text)])) as codex, socket.socket() as dead:
        dead.bind(("127.0.0.1", 0))  # bound, never listening: plain-HTTP egress is refused at once
        ca = make_test_ca(tmp_path / "ca", list(VENDOR_HOSTS))
        proxy = VendorProxy(ca, {"api.anthropic.com": ant.handler_class(),
                                 "chatgpt.com": _responses_handler_for(codex)}).start()
        try:
            home = _write_home(tmp_path, None)
            (tmp_path / "user-home").mkdir()
            monkeypatch.setenv("HOME", str(tmp_path / "user-home"))  # no ~/.claude, ~/.codex: nothing to borrow
            monkeypatch.setenv("HERMES_HOME", str(home))
            # gateway.run binds its home at import; an earlier test may have imported it under another
            # sandbox home. ``_hermes_home`` is the module's documented monkeypatch seam.
            import gateway.run as gateway_run
            monkeypatch.setattr(gateway_run, "_hermes_home", home)
            monkeypatch.delenv("HERMES_CODEX_BASE_URL", raising=False)
            for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY",
                        "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL"):
                monkeypatch.delenv(var, raising=False)
            for var in ("HTTPS_PROXY", "https_proxy"):
                monkeypatch.setenv(var, proxy.url)
            for var in ("HTTP_PROXY", "http_proxy"):
                monkeypatch.setenv(var, f"http://127.0.0.1:{dead.getsockname()[1]}")
            monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
            monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
            monkeypatch.setenv("SSL_CERT_FILE", str(ca.ca_pem))
            monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(ca.ca_pem))
            # Hermes' own HTTP clients (Codex) verify against the platform trust store, not
            # SSL_CERT_FILE; the one test seam is the verify resolver: trust ONLY the throwaway CA.
            import ssl
            test_ctx = ssl.create_default_context(cafile=str(ca.ca_pem))
            monkeypatch.setattr("agent.ssl_verify.resolve_httpx_verify", lambda **_kw: test_ctx)
            yield home, ant, codex, proxy
        finally:
            proxy.stop()


def _runner():
    """A gateway runner wired the way ``GatewayRunner.__init__`` binds config (no adapters)."""
    from gateway.config import load_gateway_config
    from gateway.run import GatewayRunner
    runner = object.__new__(GatewayRunner)
    runner._session_model_overrides = {}
    runner.config = load_gateway_config()
    return runner


def _source(chat_id, thread_id=None, parent=None):
    from gateway.config import Platform
    from gateway.session import SessionSource
    return SessionSource(platform=Platform.DISCORD, chat_id=chat_id, user_id="u-e2e", thread_id=thread_id,
                         parent_chat_id=parent)


def _build_agent(runner, source, message):
    """The turn runner's resolve → route → AIAgent construction (``_build_fresh_agent`` kwargs)."""
    from run_agent import AIAgent
    model, runtime_kwargs = runner._resolve_session_agent_runtime(source=source, user_config={})
    notice = runner._pre_agent_fallback_notice
    runner._pre_agent_fallback_notice = None
    reasoning = runner._resolve_session_reasoning_config(source=source, model=model)
    runner._service_tier = runner._resolve_session_service_tier(source=source)
    route = runner._resolve_turn_agent_config(message, model, runtime_kwargs)
    agent = AIAgent(
        model=route["model"], **route["runtime"], max_iterations=4, quiet_mode=True, verbose_logging=False,
        enabled_toolsets=["todo"], reasoning_config=reasoning, service_tier=runner._service_tier,
        request_overrides=route.get("request_overrides"), session_id=f"e2e-{time.monotonic_ns()}",
        platform="discord", chat_id=source.chat_id, chat_type="group", thread_id=source.thread_id,
        fallback_model=runner._fallback_chain_for_source(source), skip_context_files=True, skip_memory=True,
    )
    if notice:
        agent._pending_fallback_notice = notice
    return agent, notice


def _turn(agent, message):
    return agent.run_conversation(user_message=message)


def _pool(home):
    rows = json.loads((home / "auth.json").read_text())["credential_pool"]["anthropic"]
    return {r["id"]: (r.get("last_status"), r.get("last_error_code"), r.get("failure_reason")) for r in rows}


def _keys_hit(ant, since=0):
    return [r["x_api_key"] for r in ant.main_requests()[since:]]


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_general_billing_rotation_fallback_sticky_restart_and_recovery(tmp_path, monkeypatch):
    clock = Clock(monkeypatch)
    funded = {KEY1: False, KEY2: False}

    def anthropic(record):
        if funded.get(record["x_api_key"]):
            return Reply([Text("PRIMARY-OPUS")])
        return ApiError(400, "invalid_request_error", CREDIT_MSG)

    with _sandbox(tmp_path, monkeypatch, anthropic) as (home, ant, codex, _proxy):
        runner = _runner()
        general = _source(GENERAL)

        # Turn 1 — fresh agent on the Opus primary: key 1 billing → rotate → key 2 billing → channel fallback.
        agent, notice = _build_agent(runner, general, "turn one")
        assert (agent.provider, agent.model) == ("anthropic", "claude-opus-5-5") and notice is None
        assert agent.reasoning_config == {"enabled": True, "effort": "medium"}
        result = _turn(agent, "turn one")
        assert "FALLBACK-CODEX" in (result.get("final_response") or ""), result
        assert _keys_hit(ant) == [KEY1, KEY2]  # each key tried once, fill_first order
        sent = ant.main_requests()[0]["body"]
        assert sent["model"] == "claude-opus-5-5"
        assert sent.get("output_config", {}).get("effort") == "medium"
        assert "service_tier" not in sent and "speed" not in sent  # Opus: no ultrafast, standard speed
        assert ant.schema_errors() == []
        codex_body = codex.main_requests()[-1]
        assert codex_body["model"] == "gpt-6.1-sol"
        assert codex_body.get("service_tier") == "ultrafast"
        assert (codex_body.get("reasoning") or {}).get("effort") == "high"
        assert (agent.provider, agent.model) == ("openai-codex", "gpt-6.1-sol")
        assert _pool(home) == {"ak1": ("exhausted", 400, "billing"), "ak2": ("exhausted", 400, "billing")}

        # Turn 2 — same cached agent: the restore gate keeps it on Codex; no depleted key is re-hit.
        n_ant = len(ant.main_requests())
        result = _turn(agent, "turn two")
        assert "FALLBACK-CODEX" in (result.get("final_response") or "")
        assert len(ant.main_requests()) == n_ant
        assert codex.main_requests()[-1].get("service_tier") == "ultrafast"

        # Turn 3 — reconstructed process (new runner, config reloaded, new agent): resolution itself
        # routes to the channel fallback without a single Anthropic request; notice once per process.
        runner2 = _runner()
        agent3, notice3 = _build_agent(runner2, general, "turn three")
        assert (agent3.provider, agent3.model) == ("openai-codex", "gpt-6.1-sol")
        assert notice3 and "anthropic/claude-opus-5-5" in notice3
        assert agent3.reasoning_config == {"enabled": True, "effort": "high"}
        assert agent3.request_overrides.get("service_tier") == "ultrafast"
        result = _turn(agent3, "turn three")
        assert "FALLBACK-CODEX" in (result.get("final_response") or "")
        assert len(ant.main_requests()) == n_ant
        _agent3b, notice3b = _build_agent(runner2, general, "turn three b")
        assert notice3b is None
        # A thread under #general inherits the same route; another channel never sees Anthropic.
        thread_agent, _ = _build_agent(runner2, _source("thr-1", thread_id="thr-1", parent=GENERAL), "t")
        assert (thread_agent.provider, thread_agent.model) == ("openai-codex", "gpt-6.1-sol")
        other_agent, _ = _build_agent(runner2, _source(OTHER), "o")
        assert (other_agent.provider, other_agent.model) == ("openai-codex", "gpt-6-luna")
        assert other_agent._fallback_chain == []
        assert len(ant.main_requests()) == n_ant

        # 23 h later: still inside the daily bench — still no Anthropic traffic.
        clock.offset = 23 * 3600
        result = _turn(agent, "turn four")
        assert "FALLBACK-CODEX" in (result.get("final_response") or "")
        assert len(ant.main_requests()) == n_ant

        # 25 h later and funds returned (controlled): the cached agent restores Opus on its next turn.
        clock.offset = 25 * 3600
        funded[KEY1] = True
        result = _turn(agent, "turn five")
        assert "PRIMARY-OPUS" in (result.get("final_response") or ""), result
        assert (agent.provider, agent.model) == ("anthropic", "claude-opus-5-5")
        assert _keys_hit(ant, n_ant) == [KEY1]
        assert agent.request_overrides.get("service_tier") is None
        assert _pool(home)["ak1"][0] in ("ok", None)

        # A reconstructed process now resolves the primary directly too.
        agent6, notice6 = _build_agent(_runner(), general, "turn six")
        assert (agent6.provider, agent6.model) == ("anthropic", "claude-opus-5-5") and notice6 is None


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_general_transient_429_does_not_bench_for_a_day(tmp_path, monkeypatch):
    """A rate limit is not billing: no day-long bench, and a sibling key keeps serving."""
    def anthropic(record):
        if record["x_api_key"] == KEY1:
            return ApiError(429, "rate_limit_error", "Number of request tokens has exceeded your per-minute rate limit",
                            retry_after=30)
        return Reply([Text("PRIMARY-OPUS-K2")])

    with _sandbox(tmp_path, monkeypatch, anthropic) as (home, _ant, codex, _proxy):
        agent, _ = _build_agent(_runner(), _source(GENERAL), "hi")
        result = _turn(agent, "hi")
        assert "PRIMARY-OPUS-K2" in (result.get("final_response") or ""), result
        assert codex.main_requests() == []
        rows = {r["id"]: r for r in json.loads((home / "auth.json").read_text())["credential_pool"]["anthropic"]}
        assert rows["ak1"].get("failure_reason") != "billing"
        assert rows["ak2"].get("last_status") in (None, "ok")
