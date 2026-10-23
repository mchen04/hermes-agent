import hashlib
import json
import shlex
import time
from pathlib import Path

import pytest

from hermes_cli import kanban as kc
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli import kanban_pr_acceptance as acc

PR = "https://github.com/example/repo/pull/5"
AUTH_TEXT = "Michael (verbatim): yes merge it and deploy."


@pytest.fixture
def board(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for key in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_TASK", "HERMES_DELEGATED_CHILD_CONTEXT"):
        monkeypatch.delenv(key, raising=False)
    from hermes_cli import config
    from hermes_cli import profiles
    monkeypatch.setattr(config, "load_config", lambda: {"kanban": {}})
    monkeypatch.setattr(profiles, "profile_exists", lambda name: True)
    monkeypatch.setattr(kbd, "_memory_pressure_level", lambda: "unknown")
    monkeypatch.setattr(acc, "_api", lambda endpoint, **kwargs: {
        "html_url": PR, "number": 5, "state": "open", "merged_at": None, "closed_at": None, "draft": False,
        "base": {"repo": {"full_name": "example/repo"}}, "head": {"sha": "a" * 40, "ref": "feat/x"}})
    kb.init_db()
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="Local card that later publishes", assignee="forge")
        pr_comment = kb.add_comment(conn, tid, author="forge", body=f"Opened {PR}")
        auth = kb.add_comment(conn, tid, author="default", body=AUTH_TEXT)
        conn.execute("UPDATE task_comments SET created_at=? WHERE id=?", (int(time.time()) - 20, pr_comment))
    return {"task": tid, "auth": auth}


def change(conn, board, contract, **kwargs):
    from hermes_cli.kanban_contract_edit import change_completion_contract
    kwargs.setdefault("reason", "Michael authorized publication")
    kwargs.setdefault("authorization_comment_id", board["auth"])
    return change_completion_contract(conn, board["task"], contract, **kwargs)


def contract_edits(conn, task_id):
    rows = conn.execute("SELECT payload FROM task_events WHERE task_id=? AND kind='edited'", (task_id,))
    return [p for p in (json.loads(r[0]) for r in rows) if p["fields"] == ["completion_contract"]]


def stored(conn, task_id):
    return kb.get_task(conn, task_id).completion_contract


def resume(conn, board):
    from hermes_cli import kanban_pr_resume
    return kanban_pr_resume.recover_authorized_pr_resume(
        conn, board["task"], board["auth"], actor="default", reason="Resume the same owner on its PR")


def test_operator_cli_edit_lets_an_authorized_resume_proceed(board):
    tid = board["task"]
    auth = json.loads(kc.run_slash(f"show {tid} --json"))["comments"][-1]["id"]
    assert auth == board["auth"]
    with kbc.connect() as conn:
        assert stored(conn, tid) == "local-only"
        refused = resume(conn, board)
        assert refused.status == "invalid_contract"
        assert f"hermes kanban edit {tid} --completion-contract" in refused.detail
        assert kbd.check_respawn_guard(conn, tid) == "active_pr"

    reason = "Michael approved merge and deploy"
    out = kc.run_slash(f"edit {tid} --completion-contract example/repo "
                       f"--authorization-comment {auth} --reason {shlex.quote(reason)}")
    assert f"Edited {tid}: completion contract example/repo" in out

    with kbc.connect() as conn:
        assert stored(conn, tid) == "example/repo"
        (event,) = contract_edits(conn, tid)
        assert (event["from"], event["to"], event["reason"]) == ("local-only", "example/repo", reason)
        assert event["origin"] == "operator"
        assert event["authorization"]["id"] == auth
        assert event["authorization"]["author"] == "default"
        assert event["authorization"]["sha256"] == hashlib.sha256(AUTH_TEXT.encode()).hexdigest()
        assert resume(conn, board).status == "recorded"
        assert kbd.check_respawn_guard(conn, tid) is None


@pytest.mark.parametrize("before,after", [
    ("local-only", "example/repo"),
    ("local-only", PR),
    ("example/repo", PR),
    ("example/repo", "local-only"),
    (PR, "other-owner/other.repo"),
    (None, "example/repo"),
])
def test_any_valid_contract_changes_with_its_old_value_recorded(board, before, after):
    with kbc.connect() as conn:
        conn.execute("UPDATE tasks SET completion_contract=? WHERE id=?", (before, board["task"]))
        assert change(conn, board, after) is True
        assert stored(conn, board["task"]) == after
        (event,) = contract_edits(conn, board["task"])
        assert (event["from"], event["to"]) == (before, after)


@pytest.mark.parametrize("before,same", [("example/repo", "example/repo"), (None, "local-only")])
def test_an_unchanged_contract_records_nothing(board, before, same):
    with kbc.connect() as conn:
        conn.execute("UPDATE tasks SET completion_contract=? WHERE id=?", (before, board["task"]))
        assert change(conn, board, same) is False
        assert stored(conn, board["task"]) == before
        assert contract_edits(conn, board["task"]) == []


@pytest.mark.parametrize("caller", ["own_worker", "other_card_worker", "delegated_child"])
def test_workers_cannot_change_any_cards_contract(board, monkeypatch, caller):
    with kbc.connect() as conn:
        other = kb.create_task(conn, title="Other card", assignee="forge")
        if caller == "own_worker":
            monkeypatch.setenv("HERMES_KANBAN_TASK", board["task"])
        elif caller == "other_card_worker":
            monkeypatch.setenv("HERMES_KANBAN_TASK", other)
        else:
            monkeypatch.setenv("HERMES_DELEGATED_CHILD_CONTEXT", "1")
        with pytest.raises(PermissionError):
            change(conn, board, "example/repo")
        assert stored(conn, board["task"]) == "local-only"
        assert contract_edits(conn, board["task"]) == []


def test_worker_cli_edit_of_its_own_contract_is_refused(board, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", board["task"])
    out = kc.run_slash(f"edit {board['task']} --completion-contract example/repo "
                       f"--authorization-comment {board['auth']} --reason widen")
    assert "cannot change a completion contract" in out
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    with kbc.connect() as conn:
        assert stored(conn, board["task"]) == "local-only"
        assert contract_edits(conn, board["task"]) == []


@pytest.mark.parametrize("fault", ["missing", "cross_card", "worker_origin", "assignee", "unknown_author",
                                   "prior_worker"])
def test_authorization_must_be_an_operator_comment_on_the_card(board, monkeypatch, fault):
    with kbc.connect() as conn:
        if fault == "missing":
            board["auth"] += 100
        elif fault == "cross_card":
            other = kb.create_task(conn, title="Other card")
            board["auth"] = kb.add_comment(conn, other, author="default", body=AUTH_TEXT)
        elif fault == "worker_origin":
            monkeypatch.setenv("HERMES_KANBAN_TASK", board["task"])
            board["auth"] = kb.add_comment(conn, board["task"], author="default", body=AUTH_TEXT)
            monkeypatch.delenv("HERMES_KANBAN_TASK")
        elif fault == "assignee":
            conn.execute("UPDATE task_comments SET author='forge' WHERE id=?", (board["auth"],))
        elif fault == "unknown_author":
            conn.execute("UPDATE task_comments SET author='someone' WHERE id=?", (board["auth"],))
        else:
            conn.execute("INSERT INTO task_runs(task_id,profile,status,started_at,ended_at,outcome) "
                         "VALUES (?,'default','done',1,2,'completed')", (board["task"],))
        with pytest.raises(PermissionError):
            change(conn, board, "example/repo")
        assert stored(conn, board["task"]) == "local-only"
        assert contract_edits(conn, board["task"]) == []


@pytest.mark.parametrize("contract,kwargs", [
    ("not a contract", {}),
    ("https://github.com/example/repo/pull/0", {}),
    ("example/repo", {"reason": " "}),
    ("example/repo", {"reason": None}),
    ("example/repo", {"authorization_comment_id": None}),
    ("example/repo", {"authorization_comment_id": 0}),
])
def test_contract_reason_and_authorization_are_required_and_validated(board, contract, kwargs):
    with kbc.connect() as conn:
        with pytest.raises(ValueError):
            change(conn, board, contract, **kwargs)
        assert stored(conn, board["task"]) == "local-only"
        assert contract_edits(conn, board["task"]) == []


def test_a_contract_change_voids_a_resume_recorded_against_the_old_contract(board):
    with kbc.connect() as conn:
        conn.execute("UPDATE tasks SET completion_contract='example/repo' WHERE id=?", (board["task"],))
        assert resume(conn, board).status == "recorded"
        assert kbd.check_respawn_guard(conn, board["task"]) is None
        assert change(conn, board, PR) is True
        assert kbd.check_respawn_guard(conn, board["task"]) == "active_pr"
        assert resume(conn, board).status == "recorded"
        assert kbd.check_respawn_guard(conn, board["task"]) is None
