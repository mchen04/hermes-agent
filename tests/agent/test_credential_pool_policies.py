"""``credential_pool_policies``: API-key-only pools and the billing re-check bench.

A Discord channel routed to direct Anthropic API keys must never borrow a Claude Code / Hermes
OAuth login, and a key whose prepaid balance is spent must not be re-tried every hour. These tests
pin both halves: selection + seeding (``auth_types``) and the persisted bench length
(``billing_recheck_hours`` with the provider reset hint), plus cross-process consistency.
"""

from __future__ import annotations

import json
import multiprocessing
import time

import pytest

DAY = 24 * 60 * 60


def _home(tmp_path):
    home = tmp_path / "hermes"
    home.mkdir(parents=True, exist_ok=True)
    return home


def _write(tmp_path, monkeypatch, *, entries, policy=None):
    home = _home(tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    cfg = "credential_pool_policies:\n  anthropic:\n" + "".join(f"    {k}: {json.dumps(v)}\n" for k, v in (policy or {}).items()) if policy else ""
    (home / "config.yaml").write_text(cfg, encoding="utf-8")
    (home / "auth.json").write_text(json.dumps({"version": 1, "credential_pool": {"anthropic": entries}}), encoding="utf-8")
    return home


def _key(cred_id, priority, **extra):
    row = {"id": cred_id, "label": cred_id, "auth_type": "api_key", "priority": priority, "source": "manual",
           "access_token": f"sk-ant-api03-test-{cred_id}", "base_url": "https://api.anthropic.com"}
    row.update(extra)
    return row


def _billing(age_seconds, **extra):
    return {"last_status": "exhausted", "last_status_at": time.time() - age_seconds, "last_error_code": 400,
            "failure_reason": "billing", **extra}


API_ONLY = {"auth_types": ["api_key"], "billing_recheck_hours": 24}


@pytest.fixture(autouse=True)
def _no_claude_code(monkeypatch):
    """Any read of the borrowed Claude Code login under the policy is a test failure."""
    def _boom(*_a, **_k):
        raise AssertionError("Claude Code credentials must not be read under an api_key-only policy")
    monkeypatch.setattr("agent.anthropic_credentials.read_claude_code_credentials", _boom)
    monkeypatch.setattr("agent.anthropic_credentials.read_hermes_oauth_credentials", _boom)


def test_api_key_only_policy_never_seeds_or_selects_oauth(tmp_path, monkeypatch):
    oauth_row = {"id": "cc", "label": "claude_code", "auth_type": "oauth", "priority": 2, "source": "claude_code",
                 "access_token": "sk-ant-oat01-test"}
    manual_oauth = {"id": "mo", "label": "manual-oauth", "auth_type": "oauth", "priority": 3,
                    "source": "manual:hermes_pkce", "access_token": "sk-ant-oat01-test2"}
    home = _write(tmp_path, monkeypatch, entries=[_key("k1", 0), _key("k2", 1), oauth_row, manual_oauth],
                  policy=API_ONLY)
    monkeypatch.setattr("hermes_cli.auth.is_provider_explicitly_configured", lambda _p: True)
    from agent.credential_pool import load_pool

    pool = load_pool("anthropic")
    ids = [e.id for e in pool.entries()]
    assert "cc" not in ids  # seeded borrowed login dropped, never re-read
    assert "mo" in ids  # a manual row stays on disk ...
    first = pool.select()
    assert first is not None and first.id == "k1"  # ... fill_first keeps priority order
    pool.mark_exhausted_and_rotate(status_code=400, credential_id="k1", failure_reason="billing")
    second = pool.current()
    assert second is not None and second.id == "k2"
    assert pool.mark_exhausted_and_rotate(status_code=400, credential_id="k2", failure_reason="billing") is None
    assert pool.select() is None  # ... but never selected: no OAuth rescue
    assert pool.has_credentials()
    disk = json.loads((home / "auth.json").read_text())["credential_pool"]["anthropic"]
    assert {r["id"]: r.get("last_status") for r in disk if r["id"] in {"k1", "k2"}} == {
        "k1": "exhausted", "k2": "exhausted"}
    assert any(r["id"] == "mo" for r in disk)


def test_billing_bench_uses_recheck_hours_not_one_hour(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, entries=[_key("k1", 0, **_billing(2 * 3600))], policy=API_ONLY)
    from agent.credential_pool import load_pool

    pool = load_pool("anthropic")
    assert pool.select() is None  # 2h after a billing failure: still benched (default would free it at 1h)
    until = pool.next_available_at()
    assert until is not None and abs(until - (time.time() - 2 * 3600 + DAY)) < 5


def test_billing_bench_lifts_after_recheck_window(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, entries=[_key("k1", 0, **_billing(DAY + 60))], policy=API_ONLY)
    from agent.credential_pool import load_pool

    entry = load_pool("anthropic").select()
    assert entry is not None and entry.id == "k1" and entry.last_status == "ok"  # one probe per day


def test_without_policy_billing_keeps_default_hour(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, entries=[_key("k1", 0, **_billing(2 * 3600))], policy=None)
    monkeypatch.setattr("hermes_cli.auth.is_provider_explicitly_configured", lambda _p: False)
    from agent.credential_pool import load_pool

    assert load_pool("anthropic").select() is not None  # upstream 1h bench unchanged without a policy


@pytest.mark.parametrize("hint_offset, expected_offset", [
    (3 * DAY, 3 * DAY),        # plausible provider reset (e.g. spend cap date) is honoured
    (10 * 60, 3600),           # a hint shorter than the default bench is floored at 1h
    (60 * DAY, DAY),           # implausible hint (> 35 days) falls back to the daily re-check
])
def test_billing_reset_hint(tmp_path, monkeypatch, hint_offset, expected_offset):
    marked = time.time() - 30
    row = _key("k1", 0, last_status="exhausted", last_status_at=marked, last_error_code=400,
               failure_reason="billing", last_error_reset_at=marked + hint_offset)
    _write(tmp_path, monkeypatch, entries=[row], policy=API_ONLY)
    from agent.credential_pool import load_pool

    until = load_pool("anthropic").next_available_at()
    assert until is not None and abs(until - (marked + expected_offset)) < 5


def test_recheck_hours_capped_at_one_day(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, entries=[_key("k1", 0, **_billing(DAY + 60))],
           policy={"auth_types": ["api_key"], "billing_recheck_hours": 240})
    from agent.credential_pool import load_pool

    assert load_pool("anthropic").select() is not None


def test_transient_429_is_not_a_billing_bench(tmp_path, monkeypatch):
    row = _key("k1", 0, last_status="exhausted", last_status_at=time.time() - 2 * 3600, last_error_code=429,
               failure_reason="rate_limit")
    _write(tmp_path, monkeypatch, entries=[row, _key("k2", 1)], policy=API_ONLY)
    from agent.credential_pool import load_pool

    assert load_pool("anthropic").select().id == "k1"  # 429 keeps its own 1h TTL


def test_manual_reset_recovers_immediately(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, entries=[_key("k1", 0, **_billing(60)), _key("k2", 1, **_billing(60))],
           policy=API_ONLY)
    from agent.credential_pool import load_pool

    pool = load_pool("anthropic")
    assert pool.select() is None
    assert pool.reset_statuses() == 2  # `hermes auth reset anthropic`
    assert load_pool("anthropic").select().id == "k1"


def test_runtime_resolution_fails_closed_when_pool_exhausted(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, entries=[_key("k1", 0, **_billing(60)), _key("k2", 1, **_billing(60))],
           policy=API_ONLY)
    monkeypatch.setenv("ANTHROPIC_TOKEN", "sk-ant-oat01-should-never-be-used")
    from hermes_cli.auth import AuthError
    from hermes_cli.runtime_provider import resolve_runtime_provider

    with pytest.raises(AuthError) as excinfo:
        resolve_runtime_provider(requested="anthropic", target_model="claude-opus-5-5")
    assert excinfo.value.code == "credential_pool_exhausted"
    assert excinfo.value.retry_after is not None and excinfo.value.retry_after > 20 * 3600


def test_runtime_resolution_uses_pool_api_key(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, entries=[_key("k1", 0, **_billing(60)), _key("k2", 1)], policy=API_ONLY)
    from hermes_cli.runtime_provider import resolve_runtime_provider

    runtime = resolve_runtime_provider(requested="anthropic", target_model="claude-opus-5-5")
    assert runtime["provider"] == "anthropic" and runtime["api_mode"] == "anthropic_messages"
    assert runtime["api_key"] == "sk-ant-api03-test-k2"
    assert runtime["credential_pool"] is not None


def test_runtime_resolution_without_any_allowed_key_fails_closed(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, entries=[], policy=API_ONLY)
    monkeypatch.setenv("ANTHROPIC_TOKEN", "sk-ant-oat01-should-never-be-used")
    from hermes_cli.auth import AuthError
    from hermes_cli.runtime_provider import resolve_runtime_provider

    with pytest.raises(AuthError) as excinfo:
        resolve_runtime_provider(requested="anthropic", target_model="claude-opus-5-5")
    assert excinfo.value.code == "credential_pool_empty"


def test_token_resolver_never_returns_oauth_under_policy(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, entries=[], policy=API_ONLY)
    monkeypatch.setenv("ANTHROPIC_TOKEN", "sk-ant-oat01-should-never-be-used")
    from agent.anthropic_credentials import resolve_anthropic_token

    assert resolve_anthropic_token() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-env-key")
    assert resolve_anthropic_token() == "sk-ant-api03-env-key"


def _child_mark(home: str, cred_id: str, barrier) -> None:
    import os
    os.environ["HERMES_HOME"] = home
    from agent.credential_pool import load_pool
    pool = load_pool("anthropic")
    barrier.wait(timeout=30)
    pool.mark_exhausted_and_rotate(status_code=400, credential_id=cred_id, failure_reason="billing")


def test_concurrent_cross_process_exhaustion_is_not_lost(tmp_path, monkeypatch):
    """Two processes each bench a different key at the same moment; neither write may be lost."""
    home = _write(tmp_path, monkeypatch, entries=[_key("k1", 0), _key("k2", 1)], policy=API_ONLY)
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    procs = [ctx.Process(target=_child_mark, args=(str(home), cid, barrier)) for cid in ("k1", "k2")]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0
    from agent.credential_pool import load_pool

    pool = load_pool("anthropic")
    assert {e.id: e.last_status for e in pool.entries()} == {"k1": "exhausted", "k2": "exhausted"}
    assert pool.select() is None
    # A fresh process sees the persisted bench too (restart does not re-hit depleted keys).
    disk = json.loads((home / "auth.json").read_text())["credential_pool"]["anthropic"]
    assert all(r.get("failure_reason") == "billing" for r in disk)


def _child_reset(home: str, barrier) -> None:
    import os
    os.environ["HERMES_HOME"] = home
    from agent.credential_pool import load_pool
    pool = load_pool("anthropic")
    barrier.wait(timeout=30)
    pool.reset_statuses()


def test_concurrent_reset_and_exhaustion_converge(tmp_path, monkeypatch):
    """A reset racing an exhaustion of the other key ends with a consistent, parseable store."""
    home = _write(tmp_path, monkeypatch, entries=[_key("k1", 0, **_billing(60)), _key("k2", 1)], policy=API_ONLY)
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    procs = [ctx.Process(target=_child_reset, args=(str(home), barrier)),
             ctx.Process(target=_child_mark, args=(str(home), "k2", barrier))]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0
    disk = json.loads((home / "auth.json").read_text())["credential_pool"]["anthropic"]
    assert {r["id"] for r in disk} == {"k1", "k2"}
    from agent.credential_pool import load_pool

    statuses = {e.id: e.last_status for e in load_pool("anthropic").entries()}
    # Order is nondeterministic: k1 is reset (ok) unless the reset ran first and saw k2 healthy;
    # k2 ends exhausted unless the reset landed after it. Never a lost row or a torn file.
    assert statuses["k1"] in {"ok", None}
    assert statuses["k2"] in {"exhausted", "ok", None}
