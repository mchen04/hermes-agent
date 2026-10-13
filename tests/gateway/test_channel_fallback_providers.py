"""Channel-local ``fallback_providers`` (``channel_overrides.<id>.fallback_providers``).

#general runs on a direct-API Anthropic pool with a Codex fallback that no other channel (and no
other profile) may inherit. Covers: config round-trip, per-source chain selection incl. thread
inheritance, resolution-time fallback when the channel's provider cannot resolve (all keys benched),
the once-per-process notice latch, recovery, and the static fast tier re-scoped onto the fallback.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gateway.config import ChannelOverride, GatewayConfig, Platform, PlatformConfig
from gateway.run import GatewayRunner
from gateway.session import SessionSource

GENERAL = "1544027215191216150"
OTHER = "1544444248869441627"
CHAIN = [{"provider": "openai-codex", "model": "gpt-6.1-sol"}]


def _runner(profile_chain=None):
    runner = object.__new__(GatewayRunner)
    runner._session_model_overrides = {}
    runner.config = GatewayConfig(platforms={Platform.DISCORD: PlatformConfig(enabled=True, channel_overrides={
        GENERAL: ChannelOverride(model="claude-opus-5-5", provider="anthropic", service_tier="ultrafast",
                                 fallback_providers=list(CHAIN)),
        OTHER: ChannelOverride(model="gpt-6-luna", provider="openai-codex"),
    })})
    runner._refresh_fallback_model = lambda: profile_chain
    return runner


def _src(chat_id, *, thread_id=None, parent=None):
    return SessionSource(platform=Platform.DISCORD, chat_id=chat_id, user_id="u1", thread_id=thread_id,
                         parent_chat_id=parent)


def test_channel_override_round_trips_fallback_providers():
    cfg = PlatformConfig.from_dict({"channel_overrides": {GENERAL: {
        "model": "claude-opus-5-5", "provider": "anthropic", "fallback_providers": CHAIN}}})
    ov = cfg.channel_overrides[GENERAL]
    assert ov.fallback_providers == CHAIN
    assert PlatformConfig.from_dict(cfg.to_dict()).channel_overrides[GENERAL].fallback_providers == CHAIN
    assert "fallback_providers" not in ChannelOverride(model="x").to_dict()


def test_chain_is_channel_local_and_threads_inherit():
    runner = _runner(profile_chain=None)
    assert runner._fallback_chain_for_source(_src(GENERAL)) == CHAIN
    # Discord thread under #general: lookup falls back to the parent channel id.
    assert runner._fallback_chain_for_source(_src("thread-1", thread_id="thread-1", parent=GENERAL)) == CHAIN
    assert runner._fallback_chain_for_source(_src(OTHER)) is None  # other channels keep the profile chain
    assert runner._fallback_chain_for_source(_src("unconfigured")) is None
    assert runner._fallback_chain_for_source(None) is None


def test_profile_chain_still_applies_elsewhere():
    profile = [{"provider": "openrouter", "model": "x/y"}]
    runner = _runner(profile_chain=profile)
    assert runner._fallback_chain_for_source(_src(OTHER)) == profile
    assert runner._fallback_chain_for_source(_src(GENERAL)) == CHAIN


def test_empty_channel_chain_disables_fallback():
    runner = _runner(profile_chain=[{"provider": "openrouter", "model": "x/y"}])
    runner.config.platforms[Platform.DISCORD].channel_overrides[GENERAL].fallback_providers = []
    assert runner._fallback_chain_for_source(_src(GENERAL)) is None


def _codex_runtime():
    return {"provider": "openai-codex", "api_key": "codex-token", "base_url": "https://chatgpt.com/backend-api/codex",
            "api_mode": "codex_responses", "credential_pool": None, "request_overrides": {}}


def _anthropic_runtime():
    return {"provider": "anthropic", "api_key": "sk-ant-api03-test", "base_url": "https://api.anthropic.com",
            "api_mode": "anthropic_messages", "credential_pool": None, "request_overrides": {}}


def _resolve(runner, source):
    with patch("gateway.run._resolve_gateway_model", return_value="gpt-6-astra"), \
         patch("gateway.run._resolve_runtime_agent_kwargs", return_value={**_codex_runtime(), "capabilities": {}}):
        return runner._resolve_session_agent_runtime(source=source, user_config={"model": {"default": "gpt-6-astra"}})


def test_exhausted_pool_routes_to_channel_fallback_without_touching_keys():
    from hermes_cli.auth import AuthError
    runner = _runner()
    calls = []

    def fake_resolve(*, requested=None, target_model=None, explicit_base_url=None, explicit_api_key=None):
        calls.append((requested, target_model))
        if requested == "anthropic":
            raise AuthError("All anthropic pool credentials are cooling down", provider="anthropic",
                            code="credential_pool_exhausted", retry_after=80000, retryable=True)
        return _codex_runtime()

    with patch("hermes_cli.runtime_provider.resolve_runtime_provider", side_effect=fake_resolve):
        model, runtime = _resolve(runner, _src(GENERAL))
        notice_1 = runner._pre_agent_fallback_notice
        model_2, _ = _resolve(runner, _src(GENERAL))
        notice_2 = runner._pre_agent_fallback_notice
        # Other channel is untouched by #general's chain.
        model_other, runtime_other = _resolve(runner, _src(OTHER))
    assert (model, runtime["provider"]) == ("gpt-6.1-sol", "openai-codex")
    assert model_2 == "gpt-6.1-sol"
    assert notice_1 and "anthropic/claude-opus-5-5" in notice_1 and "gpt-6.1-sol" in notice_1
    assert notice_2 is None  # once per process while the route stays on its fallback
    assert (model_other, runtime_other["provider"]) == ("gpt-6-luna", "openai-codex")
    assert calls[:2] == [("anthropic", "claude-opus-5-5"), ("openai-codex", "gpt-6.1-sol")]


def test_recovered_pool_restores_primary_and_rearms_notice():
    from hermes_cli.auth import AuthError
    runner = _runner()
    state = {"exhausted": True}

    def fake_resolve(*, requested=None, target_model=None, **_kw):
        if requested == "anthropic":
            if state["exhausted"]:
                raise AuthError("cooling down", provider="anthropic", code="credential_pool_exhausted")
            return _anthropic_runtime()
        return _codex_runtime()

    with patch("hermes_cli.runtime_provider.resolve_runtime_provider", side_effect=fake_resolve):
        assert _resolve(runner, _src(GENERAL))[0] == "gpt-6.1-sol"
        state["exhausted"] = False  # funding returned / bench lifted: next turn resolves the primary
        model, runtime = _resolve(runner, _src(GENERAL))
        assert (model, runtime["provider"]) == ("claude-opus-5-5", "anthropic")
        assert runner._pre_agent_fallback_notice is None
        state["exhausted"] = True
        _resolve(runner, _src(GENERAL))
        assert runner._pre_agent_fallback_notice  # a later switch is announced again


def test_misconfigured_channel_provider_is_not_rerouted():
    """Only AuthError walks the chain; a typo'd provider still surfaces (shared walker contract)."""
    runner = _runner()

    def fake_resolve(*, requested=None, **_kw):
        raise ValueError(f"Unknown provider {requested}")

    with patch("hermes_cli.runtime_provider.resolve_runtime_provider", side_effect=fake_resolve), \
         pytest.raises(RuntimeError):
        _resolve(runner, _src(GENERAL))


def test_static_fast_tier_rescoped_on_fallback_bind():
    """Opus primary (no ultrafast) → Codex fallback gets ultrafast; restore puts the primary's back."""
    from agent.route_binding import _rescope_static_fast_overrides
    agent = SimpleNamespace(service_tier="ultrafast", model="gpt-6.1-sol", provider="openai-codex",
                            base_url="https://chatgpt.com/backend-api/codex", api_mode="codex_responses",
                            request_overrides={"extra_body": {"k": 1}})
    _rescope_static_fast_overrides(agent)
    assert agent.request_overrides == {"extra_body": {"k": 1}, "service_tier": "ultrafast"}
    opus = SimpleNamespace(service_tier="ultrafast", model="claude-opus-5-5", provider="anthropic",
                           base_url="https://api.anthropic.com", api_mode="anthropic_messages",
                           _anthropic_base_url="https://api.anthropic.com",
                           request_overrides={"service_tier": "ultrafast"})
    _rescope_static_fast_overrides(opus)
    assert "service_tier" not in opus.request_overrides and "speed" not in opus.request_overrides
    normal = SimpleNamespace(service_tier=None, request_overrides={"service_tier": "keep"})
    _rescope_static_fast_overrides(normal)
    assert normal.request_overrides == {"service_tier": "keep"}


def test_session_model_override_still_wins_in_general_and_keeps_channel_chain():
    """A persisted session ``/model`` override outranks the channel route (existing precedence);
    the channel-local fallback chain is still the one this session's agent gets."""
    runner = _runner()
    skey = "agent:main:discord:group:" + GENERAL
    runner._session_state(skey).conversation.model_override = {
        "model": "gpt-6-luna", "provider": "openai-codex", "api_key": "codex-token",
        "base_url": "https://chatgpt.com/backend-api/codex", "api_mode": "codex_responses"}
    runner._rehydrate_session_model_override = lambda _k: None
    runner._resolve_session_key_or_none = lambda source, session_key: session_key

    def fake_resolve(*, requested=None, **_kw):
        raise AssertionError(f"session override must short-circuit resolution, got {requested}")

    with patch("hermes_cli.runtime_provider.resolve_runtime_provider", side_effect=fake_resolve), \
         patch("gateway.run._resolve_gateway_model", return_value="gpt-6-astra"), \
         patch("gateway.run._credential_pool_for_provider", return_value=None):
        model, runtime = runner._resolve_session_agent_runtime(source=_src(GENERAL), session_key=skey,
                                                               user_config={})
    assert (model, runtime["provider"]) == ("gpt-6-luna", "openai-codex")
    assert runner._fallback_chain_for_source(_src(GENERAL)) == CHAIN
