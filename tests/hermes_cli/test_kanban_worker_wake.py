from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli.kanban_worker_wake import worker_wake_text

BRIEF = "Goal: ship the taxi rules.\nStop: two rounds with no new reproduced bug fixed."


@pytest.fixture
def board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    for var in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    return tmp_path


def _wake(task) -> str:
    return worker_wake_text(str(kb.kanban_db_path()), task.id)


def _respawned_card(monkeypatch) -> tuple[str, int, int]:
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="Pancake taxi rules", body=BRIEF, assignee="forge")
        for n in range(40):
            kb.add_comment(conn, tid, "forge", f"round {n} progress note " + "x" * 300)
        first = kb.claim_task(conn, tid).current_run_id
        monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
        kb.add_comment(conn, tid, "forge", "Coder report: PR #92 checks pass.")
        kb.block_task(conn, tid, reason="May I squash-merge PR #92?", kind="needs_input", expected_run_id=first)
        monkeypatch.delenv("HERMES_KANBAN_TASK")
        kb.add_comment(conn, tid, "default", "Michael (verbatim): yes merge it")
        kb.unblock_task(conn, tid)
        second = kb.claim_task(conn, tid).current_run_id
    return tid, first, second


def test_a_respawned_worker_is_told_what_changed_since_its_last_run(board, monkeypatch) -> None:
    tid, first, second = _respawned_card(monkeypatch)
    with kbc.connect_closing() as conn:
        task = kb.get_task(conn, tid)

    query = _wake(task)

    assert query.startswith(f"work kanban task {tid}: Pancake taxi rules\n")
    assert f"Run {second}. Your previous run {first} ended blocked" in query
    assert BRIEF in query
    assert f"Your last handoff (run {first}):\nMay I squash-merge PR #92?" in query
    assert "New since run" in query and "1 comment(s)" in query
    assert "comment #" in query and "default (operator)" in query and "Michael (verbatim): yes merge it" in query
    assert "- unblocked" in query
    assert "round 0 progress note" not in query and "Coder report" not in query
    assert len(query) < 2500


def test_the_wake_cursor_reads_only_what_came_after_and_the_full_read_still_works(board, monkeypatch) -> None:
    from tools import kanban_tools as kt

    tid, _first, second = _respawned_card(monkeypatch)
    with kbc.connect_closing() as conn:
        query = _wake(kb.get_task(conn, tid))
    cursor = json.loads(next(line for line in query.splitlines() if line.startswith("Cursor: "))[len("Cursor: "):])
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(second))

    quiet = json.loads(kt._handle_show({"cursor": cursor}))
    with kbc.connect_closing() as conn:
        kb.add_comment(conn, tid, "default", "Also update the changelog.")
    delta = json.loads(kt._handle_show({"cursor": cursor}))
    full = json.loads(kt._handle_show({}))

    assert (quiet["comments"], quiet["events"]) == ([], [])
    assert [c["body"] for c in delta["comments"]] == ["Also update the changelog."]
    assert full["task"]["body"] == BRIEF and full["worker_context"]
    assert len(json.dumps(full)) > 5 * len(query)


def test_a_first_run_keeps_the_plain_wake(board, monkeypatch) -> None:
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="new card", body=BRIEF, assignee="forge")
        kb.claim_task(conn, tid)
        task = kb.get_task(conn, tid)

    assert _wake(task) == f"work kanban task {tid}"
