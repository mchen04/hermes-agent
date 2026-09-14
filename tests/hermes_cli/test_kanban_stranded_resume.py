"""LOCAL-PATCH kanban-stranded-resume: a parentless dependency wait is rejected, a block from outside a live run
is refused, and the dispatcher resumes answered / timed-out explicit blocks on its own within a bounded budget."""

from __future__ import annotations

from hermes_cli.kanban_db_unblock import unblock_task

import time
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


@pytest.fixture
def kanban_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _running_task(conn, title="t", parents=()):
    tid = kb.create_task(conn, title=title, assignee="worker", parents=list(parents))
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
    claimed = kb.claim_task(conn, tid, claimer="worker")
    assert claimed is not None
    return tid


def _events(conn, tid):
    return [row["kind"] for row in conn.execute("SELECT kind FROM task_events WHERE task_id=? ORDER BY id", (tid,))]


def _status(conn, tid):
    return kb.get_task(conn, tid).status


def _age_block(conn, tid, seconds):
    """Backdate the newest blocked event so the transient timeout has elapsed."""
    with kb.write_txn(conn):
        conn.execute(
            "UPDATE task_events SET created_at = created_at - ? WHERE id = "
            "(SELECT MAX(id) FROM task_events WHERE task_id=? AND kind='blocked')", (seconds, tid),
        )


# ---------------------------------------------------------------------------
# Fake dependency waits
# ---------------------------------------------------------------------------

def test_dependency_block_without_open_parent_is_rejected(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        with pytest.raises(kb.BlockRejected) as exc:
            kb.block_task(conn, tid, reason="waiting on an external session", kind="dependency", expected_run_id=1)
        assert "transient" in str(exc.value) and "needs_input" in str(exc.value)
        assert _status(conn, tid) == "running", "the run keeps ownership; nothing was parked"
        assert "dependency_rejected" in _events(conn, tid)
        assert kb.get_task(conn, tid).current_run_id == 1


def test_dependency_block_with_open_parent_still_waits_in_todo(kanban_home):
    with kbc.connect_closing() as conn:
        parent = kb.create_task(conn, title="parent", assignee="worker")
        child = kb.create_task(conn, title="child", assignee="worker", parents=[parent])
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (child,))
        assert kb.block_task(conn, child, reason="needs parent", kind="dependency")
        assert _status(conn, child) == "todo"
        assert "dependency_wait" in _events(conn, child)


def test_dependency_block_after_parent_done_is_rejected(kanban_home):
    with kbc.connect_closing() as conn:
        parent = kb.create_task(conn, title="parent", assignee="worker")
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='done' WHERE id=?", (parent,))
        child = _running_task(conn, "child", parents=[parent])
        with pytest.raises(kb.BlockRejected):
            kb.block_task(conn, child, reason="x", kind="dependency", expected_run_id=1)


# ---------------------------------------------------------------------------
# Live-owner guard
# ---------------------------------------------------------------------------

def test_outside_block_refused_while_worker_heartbeat_is_fresh(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET last_heartbeat_at=? WHERE id=?", (int(time.time()) - 30, tid))
        with pytest.raises(kb.BlockRejected) as exc:
            kb.block_task(conn, tid, reason="outside", kind="needs_input")  # no expected_run_id: not the owner
        assert "live worker" in str(exc.value)
        assert _status(conn, tid) == "running"
        assert "block_refused_live_owner" in _events(conn, tid)


def test_outside_block_allowed_when_worker_is_stale_or_forced(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET last_heartbeat_at=?, worker_pid=NULL WHERE id=?",
                         (int(time.time()) - kb.LIVE_OWNER_GRACE_SECONDS - 60, tid))
        assert kb.block_task(conn, tid, reason="stale owner", kind="needs_input")
        assert _status(conn, tid) == "blocked"
    with kbc.connect_closing() as conn:
        tid = _running_task(conn, "forced")
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET last_heartbeat_at=? WHERE id=?", (int(time.time()), tid))
        assert kb.block_task(conn, tid, reason="forced", kind="needs_input", force=True)
        assert _status(conn, tid) == "blocked"


def test_outside_block_refused_when_worker_pid_is_alive(kanban_home):
    import os
    import socket
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        with kb.write_txn(conn):  # this test process stands in for the worker: alive on this host, stale heartbeat
            conn.execute("UPDATE tasks SET worker_pid=?, last_heartbeat_at=?, claim_lock=? WHERE id=?",
                         (os.getpid(), int(time.time()) - 7200, f"{socket.gethostname()}:1", tid))
        with pytest.raises(kb.BlockRejected) as exc:
            kb.block_task(conn, tid, reason="outside", kind="needs_input")
        assert "alive" in str(exc.value)
        assert _status(conn, tid) == "running"


def test_owner_run_can_always_block_itself(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET last_heartbeat_at=? WHERE id=?", (int(time.time()), tid))
        assert kb.block_task(conn, tid, reason="I need input", kind="needs_input", expected_run_id=1)
        assert _status(conn, tid) == "blocked"


# ---------------------------------------------------------------------------
# Automatic resume
# ---------------------------------------------------------------------------

def test_transient_block_resumes_after_resume_after(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason="waiting on mbp-main", kind="transient", expected_run_id=1, resume_after=120)
        assert kb.resume_stranded_blocks(conn) == []
        assert _status(conn, tid) == "blocked"
        _age_block(conn, tid, 121)
        resumed = kb.resume_stranded_blocks(conn)
        assert [r["task_id"] for r in resumed] == [tid]
        assert resumed[0]["trigger"] == "transient_timeout"
        assert _status(conn, tid) == "ready"
        assert _events(conn, tid)[-2:] == ["unblocked", "auto_resumed"]


def test_transient_backoff_grows_with_recurrences(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason="w", kind="transient", expected_run_id=1, resume_after=100)
        _age_block(conn, tid, 101)
        assert kb.resume_stranded_blocks(conn)
        # second same-kind block -> recurrences 2 -> waits 200s
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
        assert kb.claim_task(conn, tid, claimer="worker") is not None
        assert kb.block_task(conn, tid, reason="w", kind="transient", expected_run_id=2, resume_after=100)
        _age_block(conn, tid, 150)
        assert kb.resume_stranded_blocks(conn) == []
        _age_block(conn, tid, 60)
        assert kb.resume_stranded_blocks(conn)


def test_needs_input_resumes_when_a_comment_answers(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        kb.add_comment(conn, tid, "worker", "BLOCKED: which colour?")  # CLI-style prefix comment before the block
        assert kb.block_task(conn, tid, reason="which colour?", kind="needs_input", expected_run_id=1)
        assert kb.resume_stranded_blocks(conn) == [], "no answer yet"
        with kb.write_txn(conn):
            conn.execute("UPDATE task_events SET created_at = created_at - 5 WHERE task_id=?", (tid,))
            conn.execute("UPDATE task_comments SET created_at = created_at - 5 WHERE task_id=?", (tid,))
        kb.add_comment(conn, tid, "michael", "ANSWER: blue")
        resumed = kb.resume_stranded_blocks(conn)
        assert resumed and resumed[0]["trigger"] == "answered" and resumed[0]["author"] == "michael"
        assert _status(conn, tid) == "ready"


def test_untyped_block_resumes_on_answer_too(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason="handoff pending", expected_run_id=1)
        with kb.write_txn(conn):
            conn.execute("UPDATE task_events SET created_at = created_at - 5 WHERE task_id=?", (tid,))
        kb.add_comment(conn, tid, "forge", "owner checkpoint verified, continue")
        assert kb.resume_stranded_blocks(conn)
        assert _status(conn, tid) == "ready"


def test_operator_untyped_block_is_not_auto_resumed(kanban_home):
    """A dashboard drag or ``hermes kanban block`` without --kind parks the card; comments do not unpark it."""
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET last_heartbeat_at=?, worker_pid=NULL WHERE id=?", (int(time.time()) - 7200, tid))
        assert kb.block_task(conn, tid, reason="parked by operator")  # no run id: operator, not the worker
        with kb.write_txn(conn):
            conn.execute("UPDATE task_events SET created_at = created_at - 5 WHERE task_id=?", (tid,))
        kb.add_comment(conn, tid, "michael", "any comment")
        assert kb.resume_stranded_blocks(conn) == []
        assert _status(conn, tid) == "blocked"


def test_hold_and_swarm_comments_keep_the_card_parked(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason="q", kind="needs_input", expected_run_id=1)
        with kb.write_txn(conn):
            conn.execute("UPDATE task_events SET created_at = created_at - 5 WHERE task_id=?", (tid,))
        kb.add_comment(conn, tid, "michael", "HOLD: waiting on legal, do not resume")
        kb.add_comment(conn, tid, "forge", "[swarm:blackboard] progress note")
        kb.add_comment(conn, tid, "operator", "CHANGES REQUESTED: fix x")
        assert kb.resume_stranded_blocks(conn) == []
        assert _status(conn, tid) == "blocked"


def test_auto_resume_budget_resets_after_progress(kanban_home):
    """Six automatic resumes, then real progress (a completion), then a fresh block must resume again."""
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        run = 1
        for _ in range(kb.AUTO_RESUME_LIMIT):
            assert kb.block_task(conn, tid, reason="w", kind="transient", expected_run_id=run, resume_after=1)
            _age_block(conn, tid, 5000)
            assert kb.resume_stranded_blocks(conn)
            assert kb.claim_task(conn, tid, claimer="worker") is not None
            run += 1
        assert kb.complete_task(conn, tid, result="phase done", expected_run_id=run)
        # reopened for a new phase
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
        assert kb.claim_task(conn, tid, claimer="worker") is not None
        run += 1
        assert kb.block_task(conn, tid, reason="w", kind="transient", expected_run_id=run, resume_after=1)
        _age_block(conn, tid, 5000)
        assert kb.resume_stranded_blocks(conn), "budget reset by the completion"
        assert "auto_resume_exhausted" not in _events(conn, tid)


def test_manual_unblock_resets_budget(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        run = 1
        for _ in range(kb.AUTO_RESUME_LIMIT + 1):
            assert kb.block_task(conn, tid, reason="w", kind="transient", expected_run_id=run, resume_after=1)
            _age_block(conn, tid, 5000)
            kb.resume_stranded_blocks(conn)
            if _status(conn, tid) == "blocked":
                break
            assert kb.claim_task(conn, tid, claimer="worker") is not None
            run += 1
        assert _status(conn, tid) == "blocked" and "auto_resume_exhausted" in _events(conn, tid)
        assert unblock_task(conn, tid)  # a person steps in
        assert kb.claim_task(conn, tid, claimer="worker") is not None
        run += 1
        assert kb.block_task(conn, tid, reason="w", kind="transient", expected_run_id=run, resume_after=1)
        _age_block(conn, tid, 5000)
        assert kb.resume_stranded_blocks(conn), "human unblock resets the budget"


def test_auto_resumed_event_is_in_the_unblock_transaction(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason="w", kind="transient", expected_run_id=1, resume_after=1)
        _age_block(conn, tid, 5)
        assert kb.resume_stranded_blocks(conn)
        rows = conn.execute("SELECT kind, payload FROM task_events WHERE task_id=? ORDER BY id DESC LIMIT 2", (tid,)).fetchall()
        assert [r["kind"] for r in rows] == ["auto_resumed", "unblocked"]
        assert '"auto": true' in (rows[1]["payload"] or "")


def test_bookkeeping_comments_do_not_count_as_answers(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason="q", kind="needs_input", expected_run_id=1)
        with kb.write_txn(conn):
            conn.execute("UPDATE task_events SET created_at = created_at - 5 WHERE task_id=?", (tid,))
        kb.add_comment(conn, tid, "auto-decomposer", "split into children")
        kb.add_comment(conn, tid, "operator", "BLOCKED: still waiting")
        assert kb.resume_stranded_blocks(conn) == []
        assert _status(conn, tid) == "blocked"


def test_capability_block_never_auto_resumes(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason="no docker", kind="capability", expected_run_id=1)
        with kb.write_txn(conn):
            conn.execute("UPDATE task_events SET created_at = created_at - 5000 WHERE task_id=?", (tid,))
        kb.add_comment(conn, tid, "michael", "granted, go ahead")
        assert kb.resume_stranded_blocks(conn) == []
        assert _status(conn, tid) == "blocked"


def test_breaker_gave_up_and_done_and_review_are_untouched(kanban_home):
    with kbc.connect_closing() as conn:
        gave_up = _running_task(conn, "gave_up")
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='blocked', block_kind=NULL WHERE id=?", (gave_up,))
            kb._append_event(conn, gave_up, "gave_up", {"error": "x"})
        done = _running_task(conn, "done")
        assert kb.complete_task(conn, done, result="ok", expected_run_id=2)
        kb.add_comment(conn, done, "michael", "thanks")
        review = _running_task(conn, "review")
        assert kb.request_review(conn, review, summary="s", expected_run_id=3)
        kb.add_comment(conn, review, "michael", "looks fine")
        assert kb.resume_stranded_blocks(conn) == []
        assert "auto_resumed" not in _events(conn, gave_up), "breaker recovery is not ours"
        assert _status(conn, done) == "done"
        assert _status(conn, review) == "review"


def test_auto_resume_budget_then_exhausted_event_once(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        run = 1
        for _ in range(kb.AUTO_RESUME_LIMIT):
            assert kb.block_task(conn, tid, reason="w", kind="transient", expected_run_id=run, resume_after=1)
            _age_block(conn, tid, 5000)
            assert kb.resume_stranded_blocks(conn), "within budget"
            assert kb.claim_task(conn, tid, claimer="worker") is not None
            run += 1
        assert kb.block_task(conn, tid, reason="w", kind="transient", expected_run_id=run, resume_after=1)
        _age_block(conn, tid, 5000)
        assert kb.resume_stranded_blocks(conn) == []
        assert _status(conn, tid) == "blocked"
        assert _events(conn, tid).count("auto_resume_exhausted") == 1
        assert kb.resume_stranded_blocks(conn) == []
        assert _events(conn, tid).count("auto_resume_exhausted") == 1, "no duplicate alerts"
        # a person unblocks by hand: the task moves on normally
        assert unblock_task(conn, tid)
        assert _status(conn, tid) == "ready"
