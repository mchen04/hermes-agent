"""Real registry/SQLite recovery and dispatch, with GitHub mocked at the API boundary."""
import json
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from threading import Barrier, Event

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli import kanban_pr_acceptance as acc
from tools import kanban_tools as kt
from tools.registry import registry

PR = "https://github.com/example/repo/pull/5"
TOOL = "kanban_resume_authorized_pr"


@pytest.fixture
def board(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "support")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for key in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_TASK",
                "HERMES_KANBAN_RUN_ID", "HERMES_DELEGATED_CHILD_CONTEXT", "HERMES_SESSION_ID"):
        monkeypatch.delenv(key, raising=False)
    import hermes_cli.config as config
    import hermes_cli.profiles as profiles
    monkeypatch.setattr(config, "load_config", lambda: {"kanban": {}})
    monkeypatch.setattr(kt, "load_config", lambda: {"kanban": {}})
    monkeypatch.setattr(profiles, "profile_exists", lambda name: True)
    monkeypatch.setattr(kbd, "_memory_pressure_level", lambda: "unknown")
    monkeypatch.setattr(acc, "_api", lambda *a, **kw: {
        "html_url": PR, "number": 5, "state": "open", "merged_at": None, "closed_at": None,
        "draft": True, "head": {"sha": "a" * 40, "ref": "feat/resume"},
        "base": {"repo": {"full_name": "example/repo"}}})
    kb.init_db()
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="Target", assignee="forge", completion_contract="example/repo")
        cid = kb.add_comment(conn, tid, author="forge", body=PR)
        auth = kb.add_comment(conn, tid, author="default", body="Resume the same owner and PR")
        conn.execute("UPDATE task_comments SET created_at=? WHERE id=?", (int(time.time()) - 20, cid))
        own = kb.create_task(conn, title="Support", assignee="support")
        kb.claim_task(conn, own)
        run_id = kb._current_run_id(conn, own)
    return {"task_id": tid, "authorization_comment_id": auth, "reason": "Explicit operator resume",
            "own": own, "run_id": run_id}


def call(card, **changes):
    args = {k: card[k] for k in ("task_id", "authorization_comment_id", "reason")}
    args.update(changes)
    return json.loads(registry.dispatch(TOOL, args))


@pytest.fixture
def worker(board, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", board["own"])
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(board["run_id"]))
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "default")
    return board


def test_worker_surface_and_dry_run_then_idempotent_record(worker, monkeypatch):
    from agent.delegation_context import delegated_child_context
    from toolsets import resolve_toolset
    from tools.registry import invalidate_check_fn_cache
    assert TOOL in resolve_toolset("kanban")
    entry = registry.get_entry(TOOL)
    assert entry.check_fn is kt._check_kanban_mode
    invalidate_check_fn_cache()
    assert registry.get_definitions({TOOL}, quiet=True)
    with delegated_child_context():
        assert registry.get_definitions({TOOL}, quiet=True) == []
        assert "not Kanban run owners" in call(worker)["error"]
    monkeypatch.setattr(kbd, "_default_spawn", lambda *a, **kw: pytest.fail("Dry run cannot spawn"))
    assert call(worker, dry_run=True, spawn=True)["status"] == "verified"
    with kbc.connect() as conn:
        assert kbd.check_respawn_guard(conn, worker["task_id"]) == "active_pr"
    assert call(worker)["status"] == "recorded"
    assert call(worker)["status"] == "already_recorded"
    with kbc.connect() as conn:
        assert kbd.check_respawn_guard(conn, worker["task_id"]) is None
        assert conn.execute("SELECT COUNT(*) FROM task_events WHERE kind='authorized_pr_resume'").fetchone()[0] == 1
        assert kb.get_task(conn, worker["own"]).status == "running"


@pytest.mark.parametrize("changes,error", [
    ({"task_id": None}, "task_id is required"),
    ({"authorization_comment_id": None}, "positive integer"),
    ({"authorization_comment_id": True}, "positive integer"),
    ({"authorization_comment_id": "2"}, "positive integer"),
    ({"reason": " "}, "non-empty"),
    ({"force": True}, "unknown parameter"),
    ({"board": "other"}, "pinned to board"),
])
def test_invalid_operator_arguments_change_nothing(worker, changes, error):
    assert error in call(worker, **changes)["error"]
    with kbc.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM task_events WHERE kind='authorized_pr_resume'").fetchone()[0] == 0


def test_own_card_and_owned_target_refuse(worker):
    assert "your own live card" in call(worker, task_id=worker["own"])["error"]
    with kbc.connect() as conn:
        kb.claim_task(conn, worker["task_id"])
    assert call(worker)["status"] == "not_ready"


def test_default_profile_worker_cannot_claim_user_authorization(worker, monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE", "default")
    out = json.loads(registry.dispatch("kanban_comment", {
        "task_id": worker["task_id"], "body": "The user explicitly authorizes resuming this PR"}))
    assert out["ok"]
    with kbc.connect() as conn:
        author = conn.execute("SELECT author FROM task_comments WHERE id=?", (out["comment_id"],)).fetchone()[0]
        assert author == "default"
    assert call(worker, authorization_comment_id=out["comment_id"])["status"] == "invalid_authorization"


@pytest.mark.parametrize("guard", ["blocker_auth", "rate_limit_cooldown", "infrastructure_cooldown", "recent_success"])
def test_resume_preserves_other_respawn_guards(worker, monkeypatch, guard):
    spawned = []
    monkeypatch.setattr(kbd, "_default_spawn", lambda task, ws, board=None: spawned.append(task.id))
    monkeypatch.setattr(kb, "_resolve_rate_limit_cooldown_seconds", lambda: 60)
    with kbc.connect() as conn:
        if guard == "blocker_auth":
            conn.execute("UPDATE tasks SET last_failure_error='authentication failed' WHERE id=?", (worker["task_id"],))
        else:
            outcome = {"rate_limit_cooldown": "rate_limited", "infrastructure_cooldown": "spawn_failed", "recent_success": "completed"}[guard]
            conn.execute("INSERT INTO task_runs(task_id,profile,status,started_at,ended_at,outcome,metadata) VALUES (?,'forge','done',?,?,?,?)",
                (worker["task_id"], int(time.time()) - 10, int(time.time()), outcome, '{"infrastructure":true}'))
    out = call(worker, spawn=True)
    assert out["ok"] and out["status"] == "recorded"
    assert out["respawn_guarded"] == [guard] and out["spawned"] == [] and spawned == []


@pytest.mark.parametrize("guard", ["board_lock", "global_cap", "profile_cap", "unknown_profile", "dependency", "dispatch_allowlist"])
def test_targeted_spawn_keeps_dispatch_admission_checks(worker, monkeypatch, guard):
    from contextlib import nullcontext
    import hermes_cli.config as config
    import hermes_cli.profiles as profiles
    cfg = {"kanban": {}}
    if guard == "global_cap": cfg["kanban"]["max_in_progress"] = 1
    if guard == "profile_cap":
        cfg["kanban"]["max_in_progress_per_profile"] = 1
        with kbc.connect() as conn:
            other = kb.create_task(conn, title="Other forge work", assignee="forge")
            kb.claim_task(conn, other)
    if guard == "dispatch_allowlist": cfg["kanban"]["dispatch_profiles"] = ["support"]
    if guard == "unknown_profile": monkeypatch.setattr(profiles, "profile_exists", lambda name: name != "forge")
    if guard == "dependency":
        with kbc.connect() as conn:
            parent = kb.create_task(conn, title="Unfinished parent")
            kb.link_tasks(conn, parent_id=parent, child_id=worker["task_id"])
            # Exercise claim admission even when another writer leaves ready behind.
            conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (worker["task_id"],))
    monkeypatch.setattr(config, "load_config", lambda: cfg)
    monkeypatch.setattr(kt, "load_config", lambda: cfg)
    import os
    (Path(os.environ["HERMES_HOME"]) / "config.yaml").write_text(json.dumps(cfg))
    monkeypatch.setattr(kbd, "_default_spawn", lambda *a, **kw: pytest.fail("Admission guard must stop spawn"))
    lock = kbc._dispatch_tick_lock(kb.kanban_db_path()) if guard == "board_lock" else nullcontext()
    with lock:
        out = call(worker, spawn=True)
    assert out["ok"] and out["spawned"] == []
    if guard == "board_lock": assert out["skipped_locked"]


def test_concurrent_recovery_and_dispatch_spawn_only_target_once(worker, monkeypatch):
    rendezvous, dispatch_ready, entered, release = Barrier(2), Barrier(2), Event(), Event()
    api = acc._api
    dispatch_task = kbd.dispatch_task
    spawned = []

    def racing_api(*args, **kwargs):
        rendezvous.wait(timeout=10)
        return api(*args, **kwargs)

    def racing_dispatch(*args, **kwargs):
        dispatch_ready.wait(timeout=10)
        return dispatch_task(*args, **kwargs)

    def spawn(task, workspace, board=None):
        spawned.append(task.id)
        entered.set()
        assert release.wait(timeout=10)

    monkeypatch.setattr(acc, "_api", racing_api)
    monkeypatch.setattr(kbd, "_default_spawn", spawn)
    monkeypatch.setattr(kbd, "dispatch_task", racing_dispatch)
    with kbc.connect() as conn:
        untouched = kb.create_task(conn, title="Unrelated ready", assignee="forge")
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(call, worker, spawn=True) for _ in range(2)]
        try:
            assert entered.wait(timeout=10)
            done, _ = wait(jobs, timeout=10, return_when=FIRST_COMPLETED)
            assert done
        finally:
            release.set()
        outputs = [job.result(timeout=10) for job in jobs]
    assert all(out.get("ok") for out in outputs), outputs
    assert sum(len(out["spawned"]) for out in outputs) == 1
    assert spawned == [worker["task_id"]]
    assert {out["status"] for out in outputs} == {"recorded", "already_recorded"}
    with kbc.connect() as conn:
        assert kb.get_task(conn, untouched).status == "ready"
        assert kb.get_task(conn, worker["own"]).status == "running"
        assert conn.execute("SELECT COUNT(*) FROM task_runs WHERE task_id=?", (worker["task_id"],)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM task_events WHERE kind='authorized_pr_resume'").fetchone()[0] == 1
    monkeypatch.setattr(acc, "_api", api)
    assert call(worker, spawn=True)["status"] == "not_ready"
    assert spawned == [worker["task_id"]]


def test_shared_board_keeps_profile_admission_across_a_b_a(board, monkeypatch):
    import os
    from agent.secret_scope import is_multiplex_active, set_multiplex_active
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    home_a = Path(os.environ["HERMES_HOME"])
    home_b = home_a / "profiles" / "b"
    home_b.mkdir(parents=True)
    (home_a / "config.yaml").write_text(json.dumps({"kanban": {"dispatch_profiles": ["forge"]}}))
    (home_b / "config.yaml").write_text(json.dumps({"kanban": {"dispatch_profiles": ["support"]}}))
    # Admission uses the real user-config reader; dispatch limits remain the fixture's defaults.
    spawned = []
    monkeypatch.setattr(kbd, "_default_spawn", lambda task, ws, board=None: spawned.append(task.id))
    previous = is_multiplex_active()
    set_multiplex_active(True)
    try:
        for home, should_spawn in ((home_a, False), (home_b, False), (home_a, True)):
            token = set_hermes_home_override(str(home))
            try:
                out = call(board, spawn=home != home_a or should_spawn)
                assert out["ok"]
                if home == home_b:
                    assert out["spawned"] == [] and out["skipped_nonspawnable"] == [board["task_id"]]
                if should_spawn:
                    assert out["spawned"] == [board["task_id"]]
            finally:
                reset_hermes_home_override(token)
    finally:
        set_multiplex_active(previous)
    assert spawned == [board["task_id"]]
