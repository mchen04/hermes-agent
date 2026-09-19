"""LOCAL-PATCH kanban-stranded-resume + kanban-auto-loop: the dispatcher resumes answered blocks on a person's
comment and timed-out transient blocks on a bounded timer (10 min floor, budget of timer resumes, quota/HOLD
never on a timer); a block from outside a live run is refused; capability and operator parks never resume."""

from __future__ import annotations

import os
import socket
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
    """Backdate the newest blocked event so a timer has elapsed."""
    with kb.write_txn(conn):
        conn.execute(
            "UPDATE task_events SET created_at = created_at - ? WHERE id = "
            "(SELECT MAX(id) FROM task_events WHERE task_id=? AND kind='blocked')", (seconds, tid),
        )


def _backdate_all(conn, tid, seconds=5):
    with kb.write_txn(conn):
        conn.execute("UPDATE task_events SET created_at = created_at - ? WHERE task_id=?", (seconds, tid))
        conn.execute("UPDATE task_comments SET created_at = created_at - ? WHERE task_id=?", (seconds, tid))


def _reclaim(conn, tid):
    assert kb.claim_task(conn, tid, claimer="worker") is not None


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
# Answers resume; bookkeeping and HOLD do not
# ---------------------------------------------------------------------------

def test_needs_input_resumes_when_a_comment_answers(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        kb.add_comment(conn, tid, "worker", "BLOCKED: which colour?")  # CLI-style prefix comment before the block
        assert kb.block_task(conn, tid, reason="which colour?", kind="needs_input", expected_run_id=1)
        assert kb.resume_stranded_blocks(conn) == [], "no answer yet"
        _backdate_all(conn, tid)
        kb.add_comment(conn, tid, "michael", "ANSWER: blue")
        resumed = kb.resume_stranded_blocks(conn)
        assert resumed and resumed[0]["trigger"] == "answered" and resumed[0]["author"] == "michael"
        assert _status(conn, tid) == "ready"
        assert _events(conn, tid)[-2:] == ["unblocked", "auto_resumed"]


def test_untyped_worker_block_resumes_on_answer_too(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason="handoff pending", expected_run_id=1)
        _backdate_all(conn, tid)
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
        _backdate_all(conn, tid)
        kb.add_comment(conn, tid, "michael", "any comment")
        assert kb.resume_stranded_blocks(conn) == []
        assert _status(conn, tid) == "blocked"


def test_hold_and_bookkeeping_comments_keep_the_card_parked(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason="q", kind="needs_input", expected_run_id=1)
        _backdate_all(conn, tid)
        kb.add_comment(conn, tid, "michael", "HOLD: waiting on legal, do not resume")
        kb.add_comment(conn, tid, "forge", "[swarm:blackboard] progress note")
        kb.add_comment(conn, tid, "operator", "CHANGES REQUESTED: fix x")
        kb.add_comment(conn, tid, "auto-decomposer", "split into children")
        assert kb.resume_stranded_blocks(conn) == []
        assert _status(conn, tid) == "blocked"
        # a later plain comment lifts the hold
        kb.add_comment(conn, tid, "michael", "continue")
        assert kb.resume_stranded_blocks(conn)
        assert _status(conn, tid) == "ready"


def test_capability_block_never_auto_resumes(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason="no docker", kind="capability", expected_run_id=1)
        _backdate_all(conn, tid, 50000)
        kb.add_comment(conn, tid, "michael", "granted, go ahead")
        assert kb.resume_stranded_blocks(conn) == []
        assert _status(conn, tid) == "blocked"


# ---------------------------------------------------------------------------
# Transient timer: floor, budget, quota/HOLD gating
# ---------------------------------------------------------------------------

def test_transient_timer_is_floored_at_ten_minutes(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason="waiting on the build", kind="transient", expected_run_id=1, resume_after=120)
        _age_block(conn, tid, 121)
        assert kb.resume_stranded_blocks(conn) == [], "the worker asked for 2 min; the floor is 10"
        _age_block(conn, tid, kb.MIN_TRANSIENT_RESUME_SECONDS)
        resumed = kb.resume_stranded_blocks(conn)
        assert resumed and resumed[0]["trigger"] == "transient_timeout"
        assert resumed[0]["resume_after"] == kb.MIN_TRANSIENT_RESUME_SECONDS
        assert _status(conn, tid) == "ready"


def test_transient_block_resumes_sooner_on_a_comment(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason="waiting on mbp-main", kind="transient", expected_run_id=1, resume_after=3600)
        _backdate_all(conn, tid)
        kb.add_comment(conn, tid, "michael", "the build is done, continue")
        resumed = kb.resume_stranded_blocks(conn)
        assert resumed and resumed[0]["trigger"] == "answered"


def test_transient_hold_comment_pins_against_the_timer(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason="waiting", kind="transient", expected_run_id=1)
        _backdate_all(conn, tid)
        kb.add_comment(conn, tid, "michael", "HOLD: not tonight")
        _age_block(conn, tid, 7 * 3600)
        assert kb.resume_stranded_blocks(conn) == []
        assert _status(conn, tid) == "blocked"


def test_quota_wording_never_resumes_on_the_timer(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason="Codex usage limit reached (429), retry later",
                             kind="transient", expected_run_id=1)
        _age_block(conn, tid, 7 * 3600)
        assert kb.resume_stranded_blocks(conn) == []
        assert _status(conn, tid) == "blocked"
        _backdate_all(conn, tid)
        kb.add_comment(conn, tid, "michael", "quota is back, continue")
        assert kb.resume_stranded_blocks(conn), "a person's word still resumes it"


def test_timer_budget_counts_only_timer_resumes_and_resets_on_an_answer(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        run = 1
        for _ in range(kb.AUTO_RESUME_LIMIT):
            assert kb.block_task(conn, tid, reason="w", kind="transient", expected_run_id=run)
            _age_block(conn, tid, 5000)
            assert kb.resume_stranded_blocks(conn), "within budget"
            _reclaim(conn, tid)
            run += 1
        assert kb.block_task(conn, tid, reason="w", kind="transient", expected_run_id=run)
        _age_block(conn, tid, 5000)
        assert kb.resume_stranded_blocks(conn) == []
        assert _status(conn, tid) == "blocked"
        assert _events(conn, tid).count("auto_resume_exhausted") == 1
        assert kb.resume_stranded_blocks(conn) == []
        assert _events(conn, tid).count("auto_resume_exhausted") == 1, "no duplicate alerts"
        # a person's comment resumes it and resets the timer budget
        _backdate_all(conn, tid)
        kb.add_comment(conn, tid, "michael", "keep going")
        resumed = kb.resume_stranded_blocks(conn)
        assert resumed and resumed[0]["trigger"] == "answered"
        _reclaim(conn, tid)
        run += 1
        assert kb.block_task(conn, tid, reason="w", kind="transient", expected_run_id=run)
        _age_block(conn, tid, 5000)
        assert kb.resume_stranded_blocks(conn), "budget reset by the answer"


def test_review_events_do_not_reset_the_timer_budget_forever(kanban_home):
    """Progress events reset the budget once; without new progress the cap still trips."""
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        run = 1
        for _ in range(kb.AUTO_RESUME_LIMIT):
            assert kb.block_task(conn, tid, reason="w", kind="transient", expected_run_id=run)
            _age_block(conn, tid, 5000)
            assert kb.resume_stranded_blocks(conn)
            _reclaim(conn, tid)
            run += 1
        assert kb.request_review(conn, tid, summary="done", expected_run_id=run)
        assert kb.reopen_review_task(conn, tid)  # changes requested -> back to the implementer
        _reclaim(conn, tid)
        run += 1
        for _ in range(kb.AUTO_RESUME_LIMIT):
            assert kb.block_task(conn, tid, reason="w", kind="transient", expected_run_id=run)
            _age_block(conn, tid, 5000)
            assert kb.resume_stranded_blocks(conn), "fresh budget after the review round"
            _reclaim(conn, tid)
            run += 1
        assert kb.block_task(conn, tid, reason="w", kind="transient", expected_run_id=run)
        _age_block(conn, tid, 5000)
        assert kb.resume_stranded_blocks(conn) == []
        assert "auto_resume_exhausted" in _events(conn, tid)


def test_auto_resumed_event_is_in_the_unblock_transaction(kanban_home):
    with kbc.connect_closing() as conn:
        tid = _running_task(conn)
        assert kb.block_task(conn, tid, reason="w", kind="transient", expected_run_id=1)
        _age_block(conn, tid, 5000)
        assert kb.resume_stranded_blocks(conn)
        rows = conn.execute("SELECT kind, payload FROM task_events WHERE task_id=? ORDER BY id DESC LIMIT 2", (tid,)).fetchall()
        assert [r["kind"] for r in rows] == ["auto_resumed", "unblocked"]
        assert '"auto": true' in (rows[1]["payload"] or "")


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
