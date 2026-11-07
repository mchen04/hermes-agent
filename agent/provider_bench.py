"""LOCAL-PATCH provider-bench: probe each of a profile's routes before a card is dispatched."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

STATE_FILE = "provider-bench.json"
PROBE_TIMEOUT_S = 10
REFRESH_AFTER_S = 900
BENCH_COOLDOWN_S = 1800
BENCHED = "benched"
OK = "ok"


@dataclass(frozen=True)
class Route:
    key: str
    provider: str
    model: str
    base_url: str | None
    primary: bool


@dataclass(frozen=True)
class Verdict:
    profile: str
    usable: tuple[Route, ...]
    benched: Mapping[str, str]
    checked_at: float


def route_key(provider: Any, model: Any, base_url: Any = None) -> str:
    base = str(base_url or "").strip().rstrip("/")
    return f"{str(provider or '').strip().lower()}/{str(model or '').strip()}" + (f"@{base}" if base else "")


def state_path() -> Path:
    from hermes_constants import get_default_hermes_root
    return get_default_hermes_root() / "state" / STATE_FILE


def current_profile() -> str:
    from hermes_constants import get_hermes_home, profile_name_for_home
    return profile_name_for_home(get_hermes_home()) or "default"


def benched(profile: str) -> dict[str, str]:
    return {key: str(record.get("reason") or "") for key, record in _records(_load(), profile).items()
            if record.get("status") == BENCHED}


def verdict(profile: str, *, refresh_after: Optional[float] = REFRESH_AFTER_S) -> Verdict:
    now = time.time()
    state = _load()
    records = _records(state, profile)
    routes = _route_entries(profile)
    if refresh_after is not None:
        changed = False
        for route, entry in routes:
            record = records.get(route.key) or {}
            if not _due(record, now, refresh_after):
                continue
            status, reason, http_status = _probe(profile, route, entry)
            record = {**record, "checked_at": now}
            if status is not None:
                record.update(status=status, reason=reason, status_at=now, http_status=http_status,
                              until=now + BENCH_COOLDOWN_S if status == BENCHED else None)
            records[route.key] = record
            changed = True
        if changed:
            state[profile] = records
            _save(state)
    benched_keys = {key: str(record.get("reason") or "") for key, record in records.items()
                    if record.get("status") == BENCHED}
    usable = tuple(route for route, _ in routes if route.key not in benched_keys)
    checked = [float(r.get("checked_at") or 0) for r in records.values()]
    return Verdict(profile, usable, {route.key: benched_keys[route.key] for route, _ in routes if route.key in benched_keys},
                   max(checked) if checked else now)


def bench(profile: str, route: str, reason: str, cooldown: float = BENCH_COOLDOWN_S) -> None:
    now = time.time()
    state = _load()
    records = _records(state, profile)
    records[route] = {**(records.get(route) or {}), "status": BENCHED, "reason": str(reason)[:200],
                      "status_at": now, "checked_at": now, "until": now + cooldown}
    state[profile] = records
    _save(state)


def failed_route(agent: Any, result: Any) -> dict[str, Any]:
    fallback = getattr(agent, "_provider_fallback_route", None)
    if isinstance(fallback, (list, tuple)) and len(fallback) == 2 and getattr(agent, "_provider_fallback_active", False):
        model, provider = fallback
        base_url = result.get("base_url") if isinstance(result, dict) else getattr(agent, "base_url", None)
        return {"profile": current_profile(), "route": route_key(provider, model, base_url), "fallback": True}
    if isinstance(result, dict) and result.get("provider") and result.get("model"):
        return {"profile": current_profile(), "route": route_key(result["provider"], result["model"], result.get("base_url")),
                "fallback": False}
    return {}


def entry_for(profile: str, key: str) -> Optional[dict[str, Any]]:
    return next((entry for route, entry in _route_entries(profile) if route.key == key), None)


def _route_entries(profile: str) -> list[tuple[Route, Optional[dict[str, Any]]]]:
    try:
        with _scope(profile):
            from hermes_cli.config import load_config
            from hermes_cli.fallback_config import get_fallback_chain
            config = load_config()
    except Exception:
        logger.debug("provider bench: no routes for profile %s", profile, exc_info=True)
        return []
    routes: list[tuple[Route, Optional[dict[str, Any]]]] = []
    model_cfg = config.get("model")
    if isinstance(model_cfg, str):
        model_cfg = {"default": model_cfg}
    if isinstance(model_cfg, dict):
        model = model_cfg.get("default") or model_cfg.get("model")
        provider = str(model_cfg.get("provider") or "auto").strip().lower()
        base_url = str(model_cfg.get("base_url") or "").strip() or None
        if isinstance(model, str) and model.strip():
            routes.append((Route(route_key(provider, model, base_url), provider, model.strip(), base_url, True), None))
    for entry in get_fallback_chain(config):
        provider = str(entry.get("provider") or "").strip().lower()
        model = str(entry.get("model") or "").strip()
        base_url = str(entry.get("base_url") or "").strip() or None
        if provider and model:
            routes.append((Route(route_key(provider, model, base_url), provider, model, base_url, False), entry))
    return routes


def _scope(profile: str):
    from hermes_cli.kanban_db_dispatch import _worker_profile_scope
    from hermes_cli.profiles import resolve_profile_env
    return _worker_profile_scope(resolve_profile_env(profile))


def _due(record: dict, now: float, refresh_after: float) -> bool:
    if not record:
        return True
    if now - float(record.get("checked_at") or 0) < refresh_after:
        return False
    return record.get("status") != BENCHED or now >= float(record.get("until") or 0)


def _probe(profile: str, route: Route, entry: Optional[dict[str, Any]]) -> tuple[Optional[str], str, Optional[int]]:
    from hermes_cli.auth import AuthError, has_usable_secret
    try:
        with _scope(profile):
            from hermes_cli.fallback_config import resolve_entry_api_key
            from hermes_cli.runtime_provider import resolve_runtime_provider
            kwargs: dict[str, Any] = {"requested": route.provider, "target_model": route.model}
            if route.base_url:
                kwargs["explicit_base_url"] = route.base_url
            if entry is not None and (key := resolve_entry_api_key(entry)):
                kwargs["explicit_api_key"] = key
            runtime = resolve_runtime_provider(**kwargs)
    except AuthError as exc:
        return None, f"unresolved: {exc.code or 'auth'}", None
    except Exception as exc:
        return None, f"unresolved: {type(exc).__name__}", None
    if not has_usable_secret(runtime.get("api_key")):
        return None, "no credential to check", None
    request = _request(runtime, route.model)
    if request is None:
        return None, f"not probed: {runtime.get('api_mode') or 'unknown api_mode'}", None
    try:
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_S) as response:
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, f"unreachable: {type(exc).__name__}", None
    if status in (401, 403):
        return BENCHED, f"HTTP {status} from {route.provider}", status
    if 200 <= status < 300:
        return OK, f"HTTP {status}", status
    return None, f"HTTP {status}", status


def _request(runtime: dict[str, Any], model: str) -> Optional[urllib.request.Request]:
    base = str(runtime.get("base_url") or "").strip().rstrip("/")
    mode = str(runtime.get("api_mode") or "chat_completions")
    key = str(runtime.get("api_key"))
    if not base:
        return None
    message = [{"role": "user", "content": "ping"}]
    if mode == "anthropic_messages":
        url, body = f"{base}/messages", {"model": model, "max_tokens": 1, "messages": message}
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    elif mode in ("codex_responses", "responses"):
        url = f"{base}/responses"
        body = {"model": model, "input": "ping", "max_output_tokens": 16, "store": False, "stream": mode == "codex_responses"}
        headers = {"Authorization": f"Bearer {key}"}
    elif mode == "chat_completions":
        url, body = f"{base}/chat/completions", {"model": model, "messages": message, "max_tokens": 1}
        headers = {"Authorization": f"Bearer {key}"}
    else:
        return None
    headers["Content-Type"] = "application/json"
    return urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")


def _records(state: dict, profile: str) -> dict[str, dict]:
    records = state.get(profile)
    return {k: v for k, v in records.items() if isinstance(v, dict)} if isinstance(records, dict) else {}


def _load() -> dict:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(state: dict) -> None:
    from utils import atomic_json_write
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(path, state)
