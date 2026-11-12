"""LOCAL-PATCH kanban-pr-state-guard: ``active_pr`` holds a ready card only for an OPEN PR.

A linked PR that is merged or closed never holds. A failed GitHub lookup keeps the
conservative hold but names it ``pr_state_unknown``. A manual reclaim after the card's
own worker linked its PR restarts the card instead of stranding it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli import kanban_pr_acceptance as acc

MERGED = "https://github.com/example/repo/pull/5"
OPEN = "https://github.com/example/repo/pull/6"
CLOSED = "https://github.com/example/repo/pull/7"


@pytest.fixture
def kanban_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    import hermes_cli.config as cfgmod
    import hermes_cli.profiles as profmod

    monkeypatch.setattr(profmod, "profile_exists", lambda name: True)
    monkeypatch.setattr(cfgmod, "load_config", lambda *a, **k: {"kanban": {}})
    monkeypatch.setattr(kbd, "_memory_pressure_level", lambda: "unknown")
    kb.init_db()
    return home


@pytest.fixture
def github(monkeypatch):
    """Stand-in for GitHub: URL -> state, or an exception to raise. Records calls."""
    states: dict = {MERGED: "merged", OPEN: "open", CLOSED: "closed"}
    calls: list = []

    def lookup(url):
        calls.append(url)
        state = states[url]
        if isinstance(state, Exception):
            raise state
        return state

    monkeypatch.setattr(kbd, "_github_guard_pr_state", lookup)
    lookup.states, lookup.calls = states, calls
    return lookup


def _spawn_log():
    spawned = []

    def spawn(task, workspace):
        spawned.append(task.id)
    return spawned, spawn


def _guard_events(conn, tid):
    return [json.loads(r["payload"]) for r in conn.execute(
        "SELECT payload FROM task_events WHERE task_id = ? AND kind = 'respawn_guarded' ORDER BY id", (tid,))]


def test_merged_and_closed_prs_never_hold_and_the_dispatcher_spawns(kanban_home, github):
    spawned, spawn = _spawn_log()
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="t", assignee="forge")
        kb.add_comment(conn, tid, author="forge", body=f"PR merged: {MERGED}; old attempt closed: {CLOSED}")
        assert kbd.respawn_guard_verdict(conn, tid) == (None, {})
        res = kbd.dispatch_once(conn, spawn_fn=spawn)
        assert [s[0] for s in res.spawned] == [tid] and res.respawn_guarded == []
        assert spawned == [tid] and _guard_events(conn, tid) == []


def test_open_pr_holds_with_the_open_url_recorded(kanban_home, github):
    spawned, spawn = _spawn_log()
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="t", assignee="forge")
        kb.add_comment(conn, tid, author="forge", body=f"merged {MERGED}, follow-up draft {OPEN}")
        assert kbd.respawn_guard_verdict(conn, tid) == ("active_pr", {"open_prs": [OPEN]})
        res = kbd.dispatch_once(conn, spawn_fn=spawn)
        assert res.respawn_guarded == [(tid, "active_pr")] and spawned == []
        assert _guard_events(conn, tid) == [{"reason": "active_pr", "open_prs": [OPEN]}]


def test_failed_lookup_keeps_the_hold_as_pr_state_unknown(kanban_home, github):
    github.states[MERGED] = subprocess.TimeoutExpired(["gh"], 8)
    spawned, spawn = _spawn_log()
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="t", assignee="forge")
        kb.add_comment(conn, tid, author="forge", body=f"see {MERGED}")
        res = kbd.dispatch_once(conn, spawn_fn=spawn)
        assert res.respawn_guarded == [(tid, "pr_state_unknown")] and spawned == []
        assert _guard_events(conn, tid) == [{"reason": "pr_state_unknown", "unknown_prs": [MERGED]}]
        assert "pr_state_unknown=1" in kbd.describe_suppression([res])


def test_lookups_are_cached_per_url(kanban_home, github, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(kbd.time, "monotonic", lambda: clock[0])
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="t", assignee="forge")
        kb.add_comment(conn, tid, author="forge", body=f"draft {OPEN}")
        for _ in range(5):
            assert kbd.check_respawn_guard(conn, tid) == "active_pr"
        assert github.calls == [OPEN]
        # The PR merges; the next lookup after the open-state TTL sees it and releases the card.
        github.states[OPEN] = "merged"
        clock[0] += kbd._GUARD_PR_STATE_TTL["open"] + 1
        assert kbd.check_respawn_guard(conn, tid) is None
        assert github.calls == [OPEN, OPEN]


def _backdate(conn, cid):
    """Move a comment 10 s into the past so a later reclaim is strictly newer."""
    with kb.write_txn(conn):
        conn.execute("UPDATE task_comments SET created_at = created_at - 10 WHERE id = ?", (cid,))
    return cid


def _run_as_worker(monkeypatch, conn, tid, body):
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    try:
        return _backdate(conn, kb.add_comment(conn, tid, author="forge", body=body))
    finally:
        monkeypatch.delenv("HERMES_KANBAN_TASK")


def _claim_and_reclaim(conn, tid, *, manual=True):
    assert kb.claim_task(conn, tid) is not None
    if manual:
        assert kb.reclaim_task(conn, tid, reason="Michael gave new instructions", signal_fn=lambda *a: None)
    else:
        # A dispatcher reclaim (stale lock / crash) is recorded without ``manual``.
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='ready', claim_lock=NULL, claim_expires=NULL, "
                         "current_run_id=NULL WHERE id=?", (tid,))
            kb._append_event(conn, tid, "reclaimed", {"stale_lock": "x", "retry_status": "ready"})


def test_manual_reclaim_after_own_worker_open_pr_respawns(kanban_home, github, monkeypatch):
    spawned, spawn = _spawn_log()
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="t", assignee="forge")
        _run_as_worker(monkeypatch, conn, tid, f"opened draft {OPEN}")
        assert kbd.check_respawn_guard(conn, tid) == "active_pr"
        _claim_and_reclaim(conn, tid)
        assert conn.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0] == "ready"
        assert kbd.check_respawn_guard(conn, tid) is None
        res = kbd.dispatch_once(conn, spawn_fn=spawn)
        assert spawned == [tid], res


def test_reclaim_exception_is_narrow(kanban_home, github, monkeypatch):
    with kbc.connect() as conn:
        # A crash/stale reclaim is not an operator restart.
        crashed = kb.create_task(conn, title="crashed", assignee="forge")
        _run_as_worker(monkeypatch, conn, crashed, f"opened draft {OPEN}")
        _claim_and_reclaim(conn, crashed, manual=False)
        assert kbd.check_respawn_guard(conn, crashed) == "active_pr"
        # An open PR someone else linked still holds after a manual reclaim.
        foreign = kb.create_task(conn, title="foreign", assignee="forge")
        _backdate(conn, kb.add_comment(conn, foreign, author="default", body=f"related work in {OPEN}"))
        _claim_and_reclaim(conn, foreign)
        assert kbd.check_respawn_guard(conn, foreign) == "active_pr"


WAIT = "SCHEDULED_UNTIL=2030-01-01T00:00:00+00:00 waiting on the PR checks"


def _park_then_wake(conn, tid):
    assert kb.claim_task(conn, tid) is not None
    assert kb.schedule_task(conn, tid, reason=WAIT)
    assert kb.unblock_task(conn, tid)
    assert conn.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0] == "ready"


def _end_runs_relative_to_comment(conn, tid, cid, seconds):
    with kb.write_txn(conn):
        conn.execute(
            "UPDATE task_runs SET ended_at = (SELECT created_at FROM task_comments WHERE id = ?) + ? "
            "WHERE task_id = ? AND ended_at IS NOT NULL", (cid, seconds, tid))


def _crash(conn, tid):
    kbd._record_task_failure(conn, tid, "worker died", outcome="crashed", release_claim=True, end_run=True)


def _expire_claim(conn, tid):
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET claim_expires = 1 WHERE id = ?", (tid,))
    assert kb.release_stale_claims(conn, signal_fn=lambda *a: None) == 1


def test_own_timed_wait_after_own_worker_open_pr_respawns_on_every_wake(kanban_home, github, monkeypatch):
    spawned, spawn = _spawn_log()
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="t", assignee="forge")
        _run_as_worker(monkeypatch, conn, tid, f"opened draft {OPEN}")
        assert kbd.respawn_guard_verdict(conn, tid) == ("active_pr", {"open_prs": [OPEN]})
        _park_then_wake(conn, tid)
        assert kbd.respawn_guard_verdict(conn, tid) == (None, {})
        res = kbd.dispatch_once(conn, spawn_fn=spawn)
        assert spawned == [tid] and res.respawn_guarded == [], res
        assert kb.schedule_task(conn, tid, reason=WAIT)
        assert kb.unblock_task(conn, tid)
        assert kbd.respawn_guard_verdict(conn, tid) == (None, {})
        assert _guard_events(conn, tid) == []


@pytest.mark.parametrize("seconds_after_comment, held", [(1, None), (0, "active_pr"), (-1, "active_pr")])
def test_own_wait_lifts_only_when_the_run_ended_after_the_pr_comment(
        kanban_home, github, monkeypatch, seconds_after_comment, held):
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="t", assignee="forge")
        cid = _run_as_worker(monkeypatch, conn, tid, f"opened draft {OPEN}")
        _park_then_wake(conn, tid)
        _end_runs_relative_to_comment(conn, tid, cid, seconds_after_comment)
        assert kbd.check_respawn_guard(conn, tid) == held


@pytest.mark.parametrize("lose_worker", [_crash, _expire_claim])
def test_a_worker_lost_after_the_wake_still_holds(kanban_home, github, monkeypatch, lose_worker):
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="t", assignee="forge")
        cid = _run_as_worker(monkeypatch, conn, tid, f"opened draft {OPEN}")
        _park_then_wake(conn, tid)
        _end_runs_relative_to_comment(conn, tid, cid, 1)
        assert kbd.check_respawn_guard(conn, tid) is None
        assert kb.claim_task(conn, tid) is not None
        lose_worker(conn, tid)
        assert conn.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0] == "ready"
        assert kbd.respawn_guard_verdict(conn, tid) == ("active_pr", {"open_prs": [OPEN]})


def test_own_wait_after_someone_elses_pr_comment_still_holds(kanban_home, github):
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="t", assignee="forge")
        _backdate(conn, kb.add_comment(conn, tid, author="default", body=f"related work in {OPEN}"))
        _park_then_wake(conn, tid)
        assert kbd.respawn_guard_verdict(conn, tid) == ("active_pr", {"open_prs": [OPEN]})


def test_own_wait_with_an_unanswered_pr_lookup_still_holds(kanban_home, github, monkeypatch):
    github.states[OPEN] = subprocess.TimeoutExpired(["gh"], 8)
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="t", assignee="forge")
        _run_as_worker(monkeypatch, conn, tid, f"opened draft {OPEN}")
        _park_then_wake(conn, tid)
        assert kbd.respawn_guard_verdict(conn, tid) == ("pr_state_unknown", {"unknown_prs": [OPEN]})


@pytest.mark.real_pr_state_lookup
def test_github_lookup_reads_state_with_a_short_timeout(monkeypatch):
    seen = []
    responses = {
        "repos/example/repo/pulls/5": {"state": "closed", "merged_at": "2026-10-02T10:52:00Z"},
        "repos/example/repo/pulls/6": {"state": "open", "merged_at": None},
        "repos/example/repo/pulls/7": {"state": "closed", "merged_at": None},
    }

    def api(endpoint, **kw):
        seen.append(kw.get("timeout"))
        if endpoint not in responses:
            raise subprocess.CalledProcessError(1, ["gh"], stderr="HTTP 404")
        return responses[endpoint]
    monkeypatch.setattr(acc, "_api", api)
    assert kbd._github_guard_pr_state(MERGED) == "merged"
    assert kbd._github_guard_pr_state(OPEN) == "open"
    assert kbd._github_guard_pr_state(CLOSED) == "closed"
    assert set(seen) == {kbd._GUARD_PR_LOOKUP_TIMEOUT} and kbd._GUARD_PR_LOOKUP_TIMEOUT <= 10
    # Any failure surfaces as "unknown" through the cached wrapper, never as an exception.
    assert kbd._guard_pr_state("https://github.com/example/repo/pull/404") == "unknown"
