"""Explicit recovery for a stale ``active_pr`` hold (``recover_stale_pr_guard``).

``active_pr`` stops a ready card from re-spawning for 24h after any comment
links a GitHub PR, even once that PR is merged. The recovery lifts it only
with read-only GitHub proof that every guarding PR is closed/merged, bound to
the exact comment ids and body hashes, and only for an unowned ready card.
Every other guard, the claim CAS and the caps still apply.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli import kanban_ops

PR5 = "https://github.com/example/repo/pull/5"
PR6 = "https://github.com/example/repo/pull/6"


@pytest.fixture
def kanban_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    import hermes_cli.config as cfgmod
    import hermes_cli.profiles as profmod

    monkeypatch.setattr(profmod, "profile_exists", lambda name: True)
    monkeypatch.setattr(cfgmod, "load_config", lambda *a, **k: {"kanban": {}})
    monkeypatch.setattr(kbd, "_memory_pressure_level", lambda: "unknown")
    kb.init_db()
    return home


def _states(**by_url):
    calls = []

    def lookup(url):
        calls.append(url)
        state = by_url[url]
        if isinstance(state, Exception):
            raise state
        return {"url": url, "state": state, "merged_at": "2026-10-01T22:09:04Z" if state == "merged" else None,
                "closed_at": None}
    lookup.calls = calls
    return lookup


def _guarded_card(conn, body=f"PR #5 merged: {PR5}", assignee="forge"):
    tid = kb.create_task(conn, title="nba", assignee=assignee)
    cid = kb.add_comment(conn, tid, author="forge", body=body)
    assert kbd.check_respawn_guard(conn, tid) == "active_pr"
    return tid, cid


def _recoveries(conn, tid):
    return [json.loads(r["payload"]) for r in conn.execute(
        "SELECT payload FROM task_events WHERE task_id = ? AND kind = 'stale_pr_recovered' ORDER BY id",
        (tid,))]


def test_merged_pr_recovery_lifts_active_pr_with_bound_evidence(kanban_home):
    with kbc.connect() as conn:
        tid, cid = _guarded_card(conn)
        lookup = _states(**{PR5: "merged"})
        rec = kbd.recover_stale_pr_guard(conn, tid, actor="operator", note="human said unblock",
                                         pr_state_fn=lookup)
        assert rec.status == "recorded" and rec.ok
        assert lookup.calls == [PR5]
        assert kbd.check_respawn_guard(conn, tid) is None
        (payload,) = _recoveries(conn, tid)
        assert payload["actor"] == "operator" and payload["note"] == "human said unblock"
        assert [c["id"] for c in payload["comments"]] == [cid]
        assert len(payload["comments"][0]["sha256"]) == 64
        assert payload["prs"] == [{"url": PR5, "state": "merged",
                                   "merged_at": "2026-10-01T22:09:04Z", "closed_at": None}]


def test_closed_unmerged_pr_also_counts_as_stale(kanban_home):
    with kbc.connect() as conn:
        tid, _ = _guarded_card(conn)
        assert kbd.recover_stale_pr_guard(conn, tid, actor="op", pr_state_fn=_states(**{PR5: "closed"})).ok
        assert kbd.check_respawn_guard(conn, tid) is None


@pytest.mark.parametrize("state,status", [
    ("open", "pr_not_closed"),
    ("unknown", "pr_not_closed"),
    (None, "pr_not_closed"),
    (RuntimeError("HTTP 502"), "pr_lookup_failed"),
])
def test_open_unknown_or_failed_lookup_refuses(kanban_home, state, status):
    with kbc.connect() as conn:
        tid, _ = _guarded_card(conn)
        rec = kbd.recover_stale_pr_guard(conn, tid, actor="op", pr_state_fn=_states(**{PR5: state}))
        assert rec.status == status and not rec.ok
        assert _recoveries(conn, tid) == []
        assert kbd.check_respawn_guard(conn, tid) == "active_pr"


def test_every_guarding_pr_must_be_closed(kanban_home):
    with kbc.connect() as conn:
        tid, _ = _guarded_card(conn)
        kb.add_comment(conn, tid, author="forge", body=f"follow-up {PR6}")
        rec = kbd.recover_stale_pr_guard(conn, tid, actor="op",
                                         pr_state_fn=_states(**{PR5: "merged", PR6: "open"}))
        assert rec.status == "pr_not_closed"
        assert _recoveries(conn, tid) == []
        assert kbd.check_respawn_guard(conn, tid) == "active_pr"


def test_refuses_card_with_live_owner(kanban_home):
    with kbc.connect() as conn:
        tid, _ = _guarded_card(conn)
        assert kb.claim_task(conn, tid) is not None
        rec = kbd.recover_stale_pr_guard(conn, tid, actor="op", pr_state_fn=_states(**{PR5: "merged"}))
        assert rec.status == "not_ready"
        assert _recoveries(conn, tid) == []


def test_refuses_when_card_is_claimed_during_github_check(kanban_home):
    with kbc.connect() as conn:
        tid, _ = _guarded_card(conn)

        def lookup(url):
            with kbc.connect() as other:
                assert kb.claim_task(other, tid) is not None
            return {"state": "merged", "merged_at": "x", "closed_at": None}

        rec = kbd.recover_stale_pr_guard(conn, tid, actor="op", pr_state_fn=lookup)
        assert rec.status == "not_ready"
        assert _recoveries(conn, tid) == []


def test_refuses_when_pr_comment_lands_during_github_check(kanban_home):
    with kbc.connect() as conn:
        tid, _ = _guarded_card(conn)

        def lookup(url):
            with kbc.connect() as other:
                kb.add_comment(other, tid, author="forge", body=f"new {PR6}")
            return {"state": "merged", "merged_at": "x", "closed_at": None}

        rec = kbd.recover_stale_pr_guard(conn, tid, actor="op", pr_state_fn=lookup)
        assert rec.status == "comments_changed"
        assert _recoveries(conn, tid) == []
        assert kbd.check_respawn_guard(conn, tid) == "active_pr"


def test_new_or_edited_pr_comment_rearms_guard(kanban_home):
    with kbc.connect() as conn:
        tid, cid = _guarded_card(conn)
        assert kbd.recover_stale_pr_guard(conn, tid, actor="op", pr_state_fn=_states(**{PR5: "merged"})).ok
        kb.add_comment(conn, tid, author="human", body="plain direction, no link")
        assert kbd.check_respawn_guard(conn, tid) is None
        new_id = kb.add_comment(conn, tid, author="forge", body=f"opened {PR6}")
        assert kbd.check_respawn_guard(conn, tid) == "active_pr"
        with kb.write_txn(conn):
            conn.execute("DELETE FROM task_comments WHERE id = ?", (new_id,))
        assert kbd.check_respawn_guard(conn, tid) is None
        with kb.write_txn(conn):
            conn.execute("UPDATE task_comments SET body = ? WHERE id = ?", (f"edited {PR6}", cid))
        assert kbd.check_respawn_guard(conn, tid) == "active_pr"


def test_other_guards_still_hold_after_recovery(kanban_home):
    with kbc.connect() as conn:
        tid, _ = _guarded_card(conn)
        assert kbd.recover_stale_pr_guard(conn, tid, actor="op", pr_state_fn=_states(**{PR5: "merged"})).ok
        now = int(time.time())
        with kb.write_txn(conn):
            conn.execute("INSERT INTO task_runs (task_id, profile, status, outcome, started_at, ended_at) "
                         "VALUES (?, 'forge', 'done', 'completed', ?, ?)", (tid, now - 5, now))
        assert kbd.check_respawn_guard(conn, tid) == "recent_success"
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET last_failure_error = 'HTTP 429 rate limit' WHERE id = ?", (tid,))
        assert kbd.check_respawn_guard(conn, tid) == "blocker_auth"


def test_no_pr_guard_and_unknown_task_refuse(kanban_home):
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="plain", assignee="forge")
        assert kbd.recover_stale_pr_guard(conn, tid, actor="op", pr_state_fn=_states()).status == "no_pr_guard"
        assert kbd.recover_stale_pr_guard(conn, "t_missing", actor="op",
                                          pr_state_fn=_states()).status == "unknown_task"


def test_dry_run_records_nothing(kanban_home):
    with kbc.connect() as conn:
        tid, _ = _guarded_card(conn)
        rec = kbd.recover_stale_pr_guard(conn, tid, actor="op", record=False,
                                         pr_state_fn=_states(**{PR5: "merged"}))
        assert rec.status == "verified" and rec.prs[0]["state"] == "merged"
        assert _recoveries(conn, tid) == []
        assert kbd.check_respawn_guard(conn, tid) == "active_pr"


def test_recovery_is_idempotent_and_dispatch_spawns_exactly_one_owner(kanban_home):
    spawned = []
    with kbc.connect() as conn:
        tid, _ = _guarded_card(conn)
        other = kb.create_task(conn, title="unrelated ready card", assignee="portfolio")
        for expected in ("recorded", "already_recorded"):
            rec = kbd.recover_stale_pr_guard(conn, tid, actor="op", pr_state_fn=_states(**{PR5: "merged"}))
            assert rec.status == expected
        assert len(_recoveries(conn, tid)) == 1

        def spawn(task, workspace):
            spawned.append(task.id)
            return None

        first = kbd.dispatch_task(conn, tid, spawn_fn=spawn)
        assert [s[0] for s in first.spawned] == [tid]
        second = kbd.dispatch_task(conn, tid, spawn_fn=spawn)
        assert second.spawned == []
        assert kb.claim_task(conn, tid) is None
        assert spawned == [tid]
        row = conn.execute("SELECT status, current_run_id FROM tasks WHERE id = ?", (tid,)).fetchone()
        assert row["status"] == "running" and row["current_run_id"] is not None
        # The named card only: the unrelated ready card is untouched.
        assert conn.execute("SELECT status, claim_lock FROM tasks WHERE id = ?", (other,)).fetchone()[:] == (
            "ready", None)
        # A running card cannot be "recovered" again.
        assert kbd.recover_stale_pr_guard(conn, tid, actor="op",
                                          pr_state_fn=_states(**{PR5: "merged"})).status == "not_ready"


def test_dispatch_task_keeps_guards_caps_and_board_lock(kanban_home):
    spawn = lambda task, workspace: None  # noqa: E731
    with kbc.connect() as conn:
        tid, _ = _guarded_card(conn)
        # No recovery: still guarded, and the guard event is written as usual.
        res = kbd.dispatch_task(conn, tid, spawn_fn=spawn)
        assert res.spawned == [] and res.respawn_guarded == [(tid, "active_pr")]
        assert kbd.recover_stale_pr_guard(conn, tid, actor="op", pr_state_fn=_states(**{PR5: "merged"})).ok

        busy = kb.create_task(conn, title="busy", assignee="forge")
        assert kb.claim_task(conn, busy) is not None
        assert kbd.dispatch_task(conn, tid, spawn_fn=spawn, max_spawn=1).spawned == []
        res = kbd.dispatch_task(conn, tid, spawn_fn=spawn, max_in_progress_per_profile=1)
        assert res.spawned == [] and res.skipped_per_profile_capped == [(tid, "forge", 1)]

        with kbc._dispatch_tick_lock(kb.kanban_db_path()) as held:
            assert held
            assert kbd.dispatch_task(conn, tid, spawn_fn=spawn).skipped_locked
        assert conn.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()[0] == "ready"


def test_dispatch_task_respects_dependencies(kanban_home):
    with kbc.connect() as conn:
        parent = kb.create_task(conn, title="parent", assignee="forge")
        child = kb.create_task(conn, title="child", assignee="forge", parents=[parent])
        assert conn.execute("SELECT status FROM tasks WHERE id = ?", (child,)).fetchone()[0] == "todo"
        assert kbd.dispatch_task(conn, child, spawn_fn=lambda t, w: None).spawned == []


@pytest.mark.parametrize("payload,state", [
    ({"state": "closed", "merged_at": "2026-10-01T22:09:04Z", "closed_at": "2026-10-01T22:09:04Z"}, "merged"),
    ({"state": "closed", "merged_at": None, "closed_at": "2026-10-01T22:09:04Z"}, "closed"),
    ({"state": "open", "merged_at": None, "closed_at": None}, "open"),
    ({"message": "weird"}, "unknown"),
])
def test_github_pr_state_maps_rest_response(monkeypatch, payload, state):
    from hermes_cli import kanban_pr_acceptance as acc

    seen = []
    monkeypatch.setattr(acc, "_api", lambda endpoint, **kw: seen.append((endpoint, kw)) or payload)
    assert kbd.github_pr_state(PR5)["state"] == state
    assert seen == [("repos/example/repo/pulls/5", {})]


def test_github_pr_state_unparseable_url_is_unknown(monkeypatch):
    from hermes_cli import kanban_pr_acceptance as acc

    monkeypatch.setattr(acc, "_api", lambda *a, **k: pytest.fail("must not call GitHub"))
    assert kbd.github_pr_state("http://github.com/a/b/pull/5")["state"] == "unknown"


def _cli_args(tid, **kw):
    return argparse.Namespace(task_id=tid, dry_run=kw.get("dry_run", False), reason="ok",
                              spawn=kw.get("spawn", False), failure_limit=kbd.DEFAULT_FAILURE_LIMIT,
                              json=True, max=None)


def test_cli_dry_run_then_record_and_spawn(kanban_home, monkeypatch, capsys):
    spawned = []
    monkeypatch.setattr(kbd, "github_pr_state", _states(**{PR5: "merged"}))
    monkeypatch.setattr(kbd, "_default_spawn", lambda task, ws, board=None: spawned.append(task.id))
    with kbc.connect() as conn:
        tid, _ = _guarded_card(conn)

    assert kanban_ops._cmd_recover_stale_pr(_cli_args(tid, dry_run=True, spawn=True)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "verified" and out["dispatch"] is None and spawned == []

    assert kanban_ops._cmd_recover_stale_pr(_cli_args(tid, spawn=True)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "recorded"
    assert [s["task_id"] for s in out["dispatch"]["spawned"]] == [tid] and spawned == [tid]


def test_cli_refusal_exits_nonzero(kanban_home, monkeypatch, capsys):
    monkeypatch.setattr(kbd, "github_pr_state", _states(**{PR5: "open"}))
    with kbc.connect() as conn:
        tid, _ = _guarded_card(conn)
    assert kanban_ops._cmd_recover_stale_pr(_cli_args(tid, spawn=True)) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "pr_not_closed" and out["dispatch"] is None
