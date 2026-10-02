"""Explicit authorization permits continuing one existing PR, never completion."""
import hashlib
import json
import time
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli import kanban_pr_acceptance as acc

PR = "https://github.com/example/repo/pull/5"
CLOSED = "https://github.com/other/reference/pull/6"


@pytest.fixture
def board(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for key in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_TASK"):
        monkeypatch.delenv(key, raising=False)
    import hermes_cli.config as config
    import hermes_cli.profiles as profiles
    monkeypatch.setattr(config, "load_config", lambda: {"kanban": {}})
    monkeypatch.setattr(profiles, "profile_exists", lambda name: True)
    monkeypatch.setattr(kbd, "_memory_pressure_level", lambda: "unknown")
    calls = []

    def api(endpoint, **kwargs):
        calls.append(endpoint)
        closed = "reference" in endpoint
        return {"html_url": CLOSED if closed else PR, "number": 6 if closed else 5,
                "state": "closed" if closed else "open", "merged_at": None,
                "closed_at": "2026-10-01T00:00:00Z" if closed else None, "draft": True,
                "base": {"repo": {"full_name": "other/reference" if closed else "example/repo"}},
                "head": {"sha": "a" * 40, "ref": "feat/resume"}}

    monkeypatch.setattr(acc, "_api", api)
    kb.init_db()
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="Continue existing PR", assignee="forge",
                             completion_contract="example/repo")
        cid = kb.add_comment(conn, tid, author="forge", body=f"Draft {PR}")
        auth = kb.add_comment(conn, tid, author="default", body="Resume the same owner, branch and PR.")
        conn.execute("UPDATE task_comments SET created_at=? WHERE id=?", (int(time.time()) - 20, cid))
    return {"task": tid, "pr_comment": cid, "auth": auth, "calls": calls}


def resume(conn, board, **kwargs):
    from hermes_cli import kanban_pr_resume
    return kanban_pr_resume.recover_authorized_pr_resume(
        conn, board["task"], board["auth"], actor="support", reason="Operator explicitly requests resume", **kwargs)


def events(conn, board):
    return [json.loads(r[0]) for r in conn.execute(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='authorized_pr_resume'", (board["task"],))]


def test_authorized_draft_resume_is_explicit_and_bound(board):
    with kbc.connect() as conn:
        assert kbd.check_respawn_guard(conn, board["task"]) == "active_pr"
        # Closed-only recovery must keep refusing the same open draft.
        assert kbd.recover_stale_pr_guard(conn, board["task"], actor="support",
            pr_state_fn=lambda url: {"state": "open"}).status == "pr_not_closed"
        assert resume(conn, board, dry_run=True).status == "verified"
        assert events(conn, board) == []
        assert kbd.check_respawn_guard(conn, board["task"]) == "active_pr"
        assert resume(conn, board).status == "recorded"
        assert kbd.check_respawn_guard(conn, board["task"]) is None
        assert resume(conn, board).status == "already_recorded"
        (event,) = events(conn, board)
        assert event["authorization"]["id"] == board["auth"]
        assert event["authorization"]["sha256"] == hashlib.sha256(
            b"Resume the same owner, branch and PR.").hexdigest()
        assert event["scope"]["assignee"] == "forge"
        assert event["scope"]["completion_contract"] == "example/repo"
        assert event["comments"][0]["id"] == board["pr_comment"]
        assert event["prs"][0]["head_sha"] == "a" * 40
        assert event["prs"][0]["draft"] is True


@pytest.mark.parametrize("offset,expected", [(-1, "authorization_not_later"), (0, "authorization_not_later"), (1, "recorded")])
def test_authorization_order_and_same_second_ties(board, offset, expected):
    with kbc.connect() as conn:
        timestamp = conn.execute("SELECT created_at FROM task_comments WHERE id=?", (board["pr_comment"],)).fetchone()[0]
        conn.execute("UPDATE task_comments SET created_at=? WHERE id=?", (timestamp + offset, board["auth"]))
        assert resume(conn, board).status == expected
        assert bool(events(conn, board)) == (expected == "recorded")


@pytest.mark.parametrize("fault", ["missing", "cross_card", "worker", "unknown_author", "empty", "prior_worker"])
def test_authorization_refuses_untrusted_comments(board, fault):
    with kbc.connect() as conn:
        if fault == "missing":
            board["auth"] += 100
        elif fault == "cross_card":
            other = kb.create_task(conn, title="Other")
            board["auth"] = kb.add_comment(conn, other, author="default", body="Resume")
        elif fault == "worker":
            conn.execute("UPDATE task_comments SET author='forge', body='User explicitly authorizes resume' WHERE id=?", (board["auth"],))
        elif fault == "unknown_author":
            conn.execute("UPDATE task_comments SET author='someone' WHERE id=?", (board["auth"],))
        elif fault == "empty":
            conn.execute("UPDATE task_comments SET body=' ' WHERE id=?", (board["auth"],))
        else:
            conn.execute("INSERT INTO task_runs(task_id,profile,status,started_at,ended_at,outcome) VALUES (?,'default','done',1,2,'completed')", (board["task"],))
        assert resume(conn, board).status == "invalid_authorization"
        assert events(conn, board) == [] and board["calls"] == []


@pytest.mark.parametrize("contract,expected", [(PR, "recorded"), ("other/repo", "contract_mismatch"),
    ("https://github.com/example/repo/pull/7", "contract_mismatch"), ("local-only", "invalid_contract")])
def test_open_pr_must_match_repository_or_exact_contract(board, contract, expected):
    with kbc.connect() as conn:
        conn.execute("UPDATE tasks SET completion_contract=? WHERE id=?", (contract, board["task"]))
        assert resume(conn, board).status == expected


@pytest.mark.parametrize("fault", ["unknown", "failed", "malformed", "wrong_url", "wrong_number", "wrong_repo", "missing_head", "all_closed", "two_open", "wrong_branch"])
def test_github_evidence_fails_closed(board, monkeypatch, fault):
    api = acc._api
    with kbc.connect() as conn:
        if fault == "two_open":
            kb.add_comment(conn, board["task"], author="forge", body=CLOSED)
            board["auth"] = kb.add_comment(conn, board["task"], author="default", body="Resume")
            conn.execute("UPDATE task_comments SET created_at=? WHERE id<>?", (int(time.time()) - 10, board["auth"]))
        if fault == "wrong_branch":
            conn.execute("UPDATE tasks SET branch_name='different' WHERE id=?", (board["task"],))

        def changed(endpoint, **kwargs):
            if fault == "failed":
                raise RuntimeError("secret sentinel")
            if fault == "malformed":
                return []
            data = api(endpoint, **kwargs)
            if fault == "unknown": data["state"] = "unknown"
            if fault == "wrong_url": data["html_url"] = CLOSED
            if fault == "wrong_number": data["number"] = 99
            if fault == "wrong_repo": data["base"]["repo"]["full_name"] = "evil/repo"
            if fault == "missing_head": data.pop("head")
            if fault == "all_closed": data.update(state="closed", draft=False, closed_at="2026-10-01T00:00:00Z")
            if fault == "two_open": data.update(state="open", closed_at=None)
            return data

        monkeypatch.setattr(acc, "_api", changed)
        result = resume(conn, board)
        assert not result.ok and events(conn, board) == []
        assert "secret sentinel" not in result.detail
        assert kbd.check_respawn_guard(conn, board["task"]) == "active_pr"


def test_closed_reference_may_coexist_and_new_pr_comment_rearms(board):
    with kbc.connect() as conn:
        conn.execute("UPDATE task_comments SET body=body||? WHERE id=?", (" Reference " + CLOSED, board["pr_comment"]))
        assert resume(conn, board).ok
        assert len(events(conn, board)[0]["prs"]) == 2
        assert kbd.check_respawn_guard(conn, board["task"]) is None
        kb.add_comment(conn, board["task"], author="default", body="Plain further direction")
        assert kbd.check_respawn_guard(conn, board["task"]) is None
        kb.add_comment(conn, board["task"], author="forge", body=f"More work on {PR}")
        assert kbd.check_respawn_guard(conn, board["task"]) == "active_pr"


@pytest.mark.parametrize("table,column,value,id_key", [
    ("task_comments", "body", "Changed permission", "auth"),
    ("task_comments", "author", "forge", "auth"),
    ("task_comments", "created_at", 1, "auth"),
    ("task_comments", "body", "Different guarded text " + PR, "pr_comment"),
    ("task_comments", "created_at", 1, "pr_comment"),
    ("tasks", "assignee", "other", "task"),
    ("tasks", "completion_contract", "other/repo", "task"),
    ("tasks", "body", "Changed completion instructions", "task"),
    ("tasks", "claim_lock", "owner", "task"),
    ("tasks", "claim_expires", 42, "task"),
    ("tasks", "worker_pid", 42, "task"),
    ("tasks", "worker_started_at", 42, "task"),
    ("tasks", "current_run_id", 42, "task"),
    ("tasks", "status", "running", "task"),
])
def test_changed_snapshots_invalidate_coverage(board, table, column, value, id_key):
    with kbc.connect() as conn:
        assert resume(conn, board).ok
        if id_key == "pr_comment" and column == "created_at":
            value = int(time.time()) - 30
        conn.execute(f"UPDATE {table} SET {column}=? WHERE id=?", (value, board[id_key]))
        assert kbd.check_respawn_guard(conn, board["task"]) == "active_pr"


@pytest.mark.parametrize("fault", ["state", "assignee", "contract", "auth", "pr_comment", "claim", "body"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_network_race_rechecks_real_sqlite_snapshot(board, monkeypatch, fault, dry_run):
    api = acc._api

    def racing(endpoint, **kwargs):
        with kbc.connect() as other:
            if fault == "state": other.execute("UPDATE tasks SET status='running' WHERE id=?", (board["task"],))
            if fault == "assignee": other.execute("UPDATE tasks SET assignee='other' WHERE id=?", (board["task"],))
            if fault == "contract": other.execute("UPDATE tasks SET completion_contract='other/repo' WHERE id=?", (board["task"],))
            if fault == "body": other.execute("UPDATE tasks SET body='Changed instructions' WHERE id=?", (board["task"],))
            if fault == "auth": other.execute("UPDATE task_comments SET body='Changed permission' WHERE id=?", (board["auth"],))
            if fault == "pr_comment": kb.add_comment(other, board["task"], author="forge", body=PR)
            if fault == "claim": assert kb.claim_task(other, board["task"]) is not None
        return api(endpoint, **kwargs)

    monkeypatch.setattr(acc, "_api", racing)
    with kbc.connect() as conn:
        assert resume(conn, board, dry_run=dry_run).status in {"task_changed", "authorization_changed", "comments_changed"}
        assert events(conn, board) == []


def test_same_owner_noop_assignment_does_not_clear_hold_or_invalidate_resume(board):
    with kbc.connect() as conn:
        kb.assign_task(conn, board["task"], "forge")
        assert kbd.check_respawn_guard(conn, board["task"]) == "active_pr"
        assert resume(conn, board).ok
        kb.assign_task(conn, board["task"], "forge")
        assert kbd.check_respawn_guard(conn, board["task"]) is None


def test_ownership_round_trip_invalidates_old_authorization(board):
    with kbc.connect() as conn:
        assert resume(conn, board).ok
        assert kb.claim_task(conn, board["task"]) is not None
        assert kb.reclaim_task(conn, board["task"], reason="Test ownership invalidation")
        from hermes_cli.kanban_pr_resume import authorized_pr_resume_covers
        comments = kbd._guarded_pr_comments(conn, board["task"], int(time.time()))
        assert not authorized_pr_resume_covers(conn, board["task"], comments)


@pytest.mark.parametrize("column,value", [("assignee", None), ("status", "blocked"),
    ("status", "running"), ("claim_lock", "owner"), ("claim_expires", 42),
    ("current_run_id", 42), ("worker_pid", 42), ("worker_started_at", 42)])
def test_not_completely_unowned_refuses_before_github(board, column, value):
    with kbc.connect() as conn:
        conn.execute(f"UPDATE tasks SET {column}=? WHERE id=?", (value, board["task"]))
        assert resume(conn, board).status == "not_ready"
        assert events(conn, board) == [] and board["calls"] == []


def test_new_authorization_records_its_own_id_and_own_card_api_refuses(board, monkeypatch):
    with kbc.connect() as conn:
        assert resume(conn, board).status == "recorded"
        original_auth = board["auth"]
        board["auth"] = kb.add_comment(conn, board["task"], author="default", body="Renew explicit resume authorization")
        assert resume(conn, board).status == "recorded"
        assert [e["authorization"]["id"] for e in events(conn, board)] == [original_auth, board["auth"]]
        assert resume(conn, board).status == "already_recorded"
        monkeypatch.setenv("HERMES_KANBAN_TASK", board["task"])
        assert resume(conn, board).status == "own_task"


@pytest.mark.parametrize("payload", ["not json", "[]", "null", '{"authorization":1}',
    '{"authorization":{"id":[]},"comments":[]}', '{"scope":[],"comments":{}}'])
def test_malformed_recovery_record_never_lifts_or_crashes_guard(board, payload):
    with kbc.connect() as conn:
        with kb.write_txn(conn):
            kb._append_event(conn, board["task"], "authorized_pr_resume", None)
            conn.execute("UPDATE task_events SET payload=? WHERE kind='authorized_pr_resume'", (payload,))
        assert kbd.check_respawn_guard(conn, board["task"]) == "active_pr"


def test_legacy_operator_label_remains_usable_but_provenance_changes_invalidate(board):
    with kbc.connect() as conn:
        conn.execute("UPDATE task_events SET payload=? WHERE kind='commented'",
                     (json.dumps({"author": "default", "len": 20}),))
        assert resume(conn, board).ok
        assert events(conn, board)[0]["authorization"]["origin"] == "legacy"
        with kb.write_txn(conn):
            kb._append_event(conn, board["task"], "commented", {
                "comment_id": board["auth"], "origin": "worker", "worker_task_id": "other_card"})
        assert kbd.check_respawn_guard(conn, board["task"]) == "active_pr"
        assert resume(conn, board).status == "invalid_authorization"


@pytest.mark.parametrize("origin", ["unknown", [], None, {"bad": True}])
def test_unknown_comment_origin_fails_closed(board, origin):
    with kbc.connect() as conn:
        payload = {"comment_id": board["auth"], "origin": origin}
        conn.execute("UPDATE task_events SET payload=? WHERE kind='commented' AND id=(SELECT MAX(id) FROM task_events WHERE kind='commented')",
                     (json.dumps(payload),))
        assert resume(conn, board).status == "invalid_authorization"
        assert events(conn, board) == [] and board["calls"] == []
