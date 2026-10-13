"""Runtime-provider credentials → ``AIAgent`` constructor kwargs for gateway sessions (profile default
route, a channel/session provider override, and their resolution-time fallback chains).

Split out of ``gateway/run.py``, which re-exports these names: callers do ``from gateway.run import ...``
at call time and tests patch ``gateway.run.<name>``. The profile config loader is read through
``gateway.run`` at call time for the same reason.
"""

from __future__ import annotations

from typing import Optional

from hermes_cli.fallback_config import pre_agent_fallback_notice


def _resolve_runtime_agent_kwargs() -> dict:
    """Resolve provider credentials for gateway-created AIAgent instances.
    ``resolve_runtime_provider()`` may fall back to env vars; behavioral config is config.yaml only.
    An ``AuthError`` from the primary walks the configured fallback chain through the shared
    ``resolve_runtime_with_fallback`` (the gateway keeps no resolver loop of its own)."""
    from gateway import run as gateway_run
    from hermes_cli.runtime_provider import (
        resolve_runtime_with_fallback, format_runtime_provider_error, _get_model_config)

    # Capture primary provider/model from config before the try block so we
    # can include it in the fallback notice if the primary fails (#74349).
    _model_cfg = _get_model_config()
    _primary_model = (_model_cfg.get("default") or "").strip()
    _primary_provider = (_model_cfg.get("provider") or "").strip()

    try:
        runtime, fallback_entry = resolve_runtime_with_fallback(gateway_run._load_gateway_config())
    except Exception as exc:
        raise RuntimeError(format_runtime_provider_error(exc)) from exc

    if fallback_entry is not None:
        # The entry's model is the one this agent must send (#112600). Carry the fallback notice so the
        # gateway can surface a user-visible provider switch (#74349); the caller must pop
        # ``_fallback_notice`` before forwarding kwargs to AIAgent.
        return {**_runtime_agent_kwargs(runtime), "model": fallback_entry["model"],
                "_fallback_notice": pre_agent_fallback_notice(
                    _primary_provider, _primary_model,
                    runtime.get("provider") or fallback_entry.get("provider") or "unknown",
                    fallback_entry.get("model") or "default")}

    capabilities = runtime.get("capabilities")
    capabilities = (
        {k: v for k, v in capabilities.items() if isinstance(k, str) and isinstance(v, bool)}
        if isinstance(capabilities, dict) else {})

    return {**_runtime_agent_kwargs(runtime), "capabilities": capabilities}


def _runtime_agent_kwargs(runtime: dict) -> dict:
    """AIAgent constructor kwargs shared by every runtime-provider resolution.
    ``request_overrides`` passes through as resolved so the provider's request body reaches each turn."""
    return {
        "api_key": runtime.get("api_key"),
        "base_url": runtime.get("base_url"),
        "provider": runtime.get("provider"),
        "requested_provider": runtime.get("requested_provider"),
        "api_mode": runtime.get("api_mode"),
        "command": runtime.get("command"),
        "args": list(runtime.get("args") or []),
        "credential_pool": runtime.get("credential_pool"),
        "request_overrides": runtime.get("request_overrides")}


def _resolve_runtime_agent_kwargs_for_provider(
    provider: str, target_model: Optional[str] = None, fallback_chain: Optional[list] = None,
) -> dict:
    """Resolve runtime credentials for a specific provider (e.g. from channel override).

    ``target_model`` is the model the override will actually send: the ladder's model-keyed rungs
    (Zen/Go relay + api_mode) must see it rather than config's ``default``, or a Go-only override
    resolves an api_mode/base_url the sent model cannot use (#112600).

    ``fallback_chain`` (a channel's ``fallback_providers``): an ``AuthError`` from *provider* — e.g. an
    API-key-only pool whose keys are all benched — walks that chain through the shared
    ``resolve_runtime_with_fallback``; the result then carries the entry's ``model`` and a
    ``_fallback_notice`` exactly like the profile-level path in :func:`_resolve_runtime_agent_kwargs`."""
    from hermes_cli.runtime_provider import (
        resolve_runtime_provider, resolve_runtime_with_fallback, format_runtime_provider_error)
    fallback_entry = None
    try:
        if fallback_chain:
            runtime, fallback_entry = resolve_runtime_with_fallback(
                {"fallback_providers": list(fallback_chain)}, requested=provider, target_model=target_model or None)
        else:
            runtime = resolve_runtime_provider(requested=provider, target_model=target_model or None)
    except Exception as exc:
        raise RuntimeError(format_runtime_provider_error(exc)) from exc
    if fallback_entry is not None:
        return {
            **_runtime_agent_kwargs(runtime), "model": fallback_entry["model"],
            "request_overrides": dict(runtime.get("request_overrides") or {}),
            "capabilities": dict(runtime.get("capabilities") or {}),
            "_fallback_notice": pre_agent_fallback_notice(
                provider, target_model, runtime.get("provider") or fallback_entry.get("provider") or "unknown",
                fallback_entry.get("model") or "default"),
        }
    return {
        **_runtime_agent_kwargs(runtime),
        "request_overrides": dict(runtime.get("request_overrides") or {}),
        "capabilities": dict(runtime.get("capabilities") or {})}
