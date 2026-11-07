"""LOCAL-PATCH provider-bench: a profile's routes are probed with their own credential before a
card is dispatched; a rejected route is benched, the run takes a usable one, and a card with no
usable route waits without a failure counted."""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from agent import provider_bench
from agent.provider_bench import bench, failed_route, route_key, verdict
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli.turn_exit import turn_exit_code

ALPHA = route_key("alpha", "m-alpha")
BETA = route_key("beta", "m-beta")


@pytest.fixture
def upstream():
    statuses = {"alpha": 200, "beta": 200}
    seen: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            name = self.path.strip("/").split("/", 1)[0]
            seen.append((self.path, self.headers.get("Authorization", "")))
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            assert json.loads(body)["max_tokens"] == 1
            self.send_response(statuses[name])
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}", statuses=statuses, seen=seen)
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def home(tmp_path, monkeypatch, upstream):
    root = tmp_path / ".hermes"
    root.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for var in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_TASK"):
        monkeypatch.delenv(var, raising=False)
    (root / "config.yaml").write_text(json.dumps({
        "model": {"provider": "alpha", "default": "m-alpha"},
        "providers": {"alpha": {"api": f"{upstream.url}/alpha", "key_env": "ALPHA_KEY"},
                      "beta": {"api": f"{upstream.url}/beta", "key_env": "BETA_KEY"}},
        "fallback_providers": [{"provider": "beta", "model": "m-beta"}],
    }))
    (root / ".env").write_text("ALPHA_KEY=alpha-secret-key\nBETA_KEY=beta-secret-key\n")
    import hermes_cli.profiles as profmod
    monkeypatch.setattr(profmod, "profile_exists", lambda name: True)
    monkeypatch.setattr(kbd, "_memory_pressure_level", lambda: "unknown")
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    return root


def _card(assignee="default") -> str:
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="probe me", assignee=assignee)
        kb.recompute_ready(conn)
    return tid


def _tick():
    spawned = []
    with kbc.connect_closing() as conn:
        result = kbd.dispatch_once(conn, max_spawn=1, spawn_fn=lambda task, workspace: spawned.append(task.id) or 4242)
    return result, spawned


def _card_state(tid):
    with kbc.connect_closing() as conn:
        row = dict(conn.execute("SELECT status, consecutive_failures FROM tasks WHERE id = ?", (tid,)).fetchone())
        comments = [r[0] for r in conn.execute("SELECT body FROM task_comments WHERE task_id = ? ORDER BY id", (tid,))]
        events = [r[0] for r in conn.execute("SELECT kind FROM task_events WHERE task_id = ? ORDER BY id", (tid,))]
    return row, comments, events


def _policy(home, tid, config):
    from gateway.session_contract import SessionActor
    from gateway.session_kanban import build_kanban_policy
    with kbc.connect_closing() as conn:
        task = kb.get_task(conn, tid)
    assert task.status == "running" and task.claim_lock
    actor = SessionActor("owner", str(home), frozenset({"session:create"}), "transport")
    connection = SimpleNamespace(authority=SimpleNamespace(profile_id=str(home)), actor=actor, native_owner=True)
    params = dict(board=kb.get_current_board(), task_id=task.id, run_id=task.current_run_id, claim_lock=task.claim_lock)
    return build_kanban_policy(connection, params, config)[0]


def _state_file(home):
    return json.loads((home / "state" / "provider-bench.json").read_text())


def test_a_rejected_fallback_is_benched_and_the_run_stays_on_the_primary(home, upstream, monkeypatch):
    upstream.statuses["beta"] = 401

    checked = verdict("default")

    assert [r.key for r in checked.usable] == [ALPHA]
    assert checked.benched == {BETA: "HTTP 401 from beta"}
    assert sorted(upstream.seen) == [("/alpha/chat/completions", "Bearer alpha-secret-key"),
                                     ("/beta/chat/completions", "Bearer beta-secret-key")]
    assert _state_file(home)["default"][BETA]["status"] == "benched"

    tid = _card()
    result, spawned = _tick()
    assert spawned == [tid] and result.respawn_guarded == []
    assert len(upstream.seen) == 2

    from agent.agent_init_fallback import _init_fallback_chain
    agent = SimpleNamespace(quiet_mode=True)
    monkeypatch.setattr("agent.agent_runtime_helpers.sync_credential_pool_entry_id", lambda agent: None)
    _init_fallback_chain(agent, [{"provider": "beta", "model": "m-beta"}, {"provider": "gamma", "model": "m-gamma"}])
    assert agent._fallback_chain == [{"provider": "gamma", "model": "m-gamma"}]


def test_a_rejected_primary_routes_the_run_to_the_fallback_with_one_comment(home, upstream):
    upstream.statuses["alpha"] = 401
    checked = verdict("default")
    assert [r.key for r in checked.usable] == [BETA] and set(checked.benched) == {ALPHA}

    tid = _card()
    result, spawned = _tick()
    assert spawned == [tid] and result.respawn_guarded == []
    policy = _policy(home, tid, {"model": {"provider": "alpha", "default": "m-alpha"}})

    assert (policy.provider, policy.model) == ("beta", "m-beta")
    _row, comments, _events = _card_state(tid)
    assert len(comments) == 1
    assert comments[0].startswith("Primary alpha rejected the credential at ") and comments[0].endswith("; this run uses beta.")


def test_both_rejected_holds_the_card_without_counting_a_failure(home, upstream):
    upstream.statuses.update(alpha=401, beta=401)
    tid = _card()

    first, spawned = _tick()
    second, spawned_later = _tick()

    assert spawned == [] and spawned_later == []
    assert first.respawn_guarded == [(tid, "credential_rejected")] == second.respawn_guarded
    row, comments, events = _card_state(tid)
    assert (row["status"], row["consecutive_failures"]) == ("ready", 0)
    assert events.count("respawn_guarded") == 1 and "gave_up" not in events
    assert len(comments) == 1
    assert "every route of profile default" in comments[0]
    assert f"{ALPHA} (HTTP 401 from alpha)" in comments[0] and f"{BETA} (HTTP 401 from beta)" in comments[0]
    assert len(upstream.seen) == 2


def test_an_unreachable_probe_leaves_the_record_alone(home, upstream, monkeypatch):
    bench("default", BETA, "HTTP 401 from beta", cooldown=0)
    unreachable = SimpleNamespace(url="http://127.0.0.1:1")
    (home / "config.yaml").write_text(json.dumps({
        "model": {"provider": "alpha", "default": "m-alpha"},
        "providers": {"alpha": {"api": f"{unreachable.url}/alpha", "key_env": "ALPHA_KEY"},
                      "beta": {"api": f"{unreachable.url}/beta", "key_env": "BETA_KEY"}},
        "fallback_providers": [{"provider": "beta", "model": "m-beta"}],
    }))
    monkeypatch.setattr(provider_bench, "PROBE_TIMEOUT_S", 2)

    checked = verdict("default", refresh_after=0)

    assert [r.key for r in checked.usable] == [ALPHA]
    assert checked.benched == {BETA: "HTTP 401 from beta"}
    records = _state_file(home)["default"]
    assert records[BETA]["status"] == "benched" and "status" not in records[ALPHA]
    assert records[ALPHA]["checked_at"] >= time.time() - 60

    monkeypatch.setattr(provider_bench, "_probe", lambda *a: pytest.fail("a fresh record is not re-probed before refresh_after"))
    assert [r.key for r in verdict("default").usable] == [ALPHA]


def test_a_credential_failure_on_a_fallback_benches_it_and_requeues(home, upstream):
    result = {"failed": True, "failure_reason": "auth", "provider": "beta", "model": "m-beta", "base_url": f"{upstream.url}/beta"}
    agent = SimpleNamespace(_provider_fallback_active=True, _provider_fallback_route=("m-beta", "beta"))

    assert failed_route(agent, result) == {"profile": "default", "route": route_key("beta", "m-beta", result["base_url"]), "fallback": True}
    assert turn_exit_code(result, kanban_worker=True, **failed_route(agent, result)) == kb.KANBAN_RATE_LIMIT_EXIT_CODE
    assert route_key("beta", "m-beta", result["base_url"]) in provider_bench.benched("default")


def test_a_rejected_primary_requeues_only_while_a_fallback_is_usable(home, upstream):
    primary = SimpleNamespace(_provider_fallback_active=False, _provider_fallback_route=None)
    result = {"failed": True, "failure_reason": "auth", "provider": "alpha", "model": "m-alpha"}

    assert turn_exit_code(result, kanban_worker=True, **failed_route(primary, result)) == kb.KANBAN_RATE_LIMIT_EXIT_CODE
    assert provider_bench.benched("default") == {ALPHA: "auth"}

    bench("default", BETA, "HTTP 401 from beta")
    assert turn_exit_code(result, kanban_worker=True, **failed_route(primary, result)) == kb.KANBAN_TERMINAL_PROVIDER_EXIT_CODE
    assert turn_exit_code(result, kanban_worker=True) == kb.KANBAN_TERMINAL_PROVIDER_EXIT_CODE
