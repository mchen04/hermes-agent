"""Atomic transitions out of a blocked or scheduled task."""
import sqlite3
import time
from typing import Optional


def unblock_task(conn: sqlite3.Connection, task_id: str, *, auto_resume: Optional[dict] = None,
                 capacity: Optional[dict] = None, expected_event_id: Optional[int] = None) -> bool:
    """``blocked``/``scheduled`` -> its resumable phase (parent re-gated; ``review``
    when that is where it left off), closing any leaked run first. ``auto_resume``
    (dispatcher only) records an ``auto_resumed`` event in the same transaction and
    marks the ``unblocked`` event as automatic, so a human unblock stays distinguishable."""
    from hermes_cli import kanban_db as kb
    now = int(time.time())
    with kb.write_txn(conn):
        if capacity is not None or auto_resume is not None:
            from hermes_cli.kanban_recovery import capacity_allows
            row = conn.execute("SELECT assignee, status FROM tasks WHERE id=?", (task_id,)).fetchone()
            event = conn.execute(
                "SELECT id, kind, payload, created_at FROM task_events WHERE task_id=? "
                "AND kind IN ('blocked','gave_up','unblocked','block_loop_detected','auto_resumed') "
                "ORDER BY id DESC LIMIT 1", (task_id,),
            ).fetchone()
            expected = expected_event_id
            if capacity is None and auto_resume is not None:
                expected = auto_resume.get('blocked_event_id')
            if (not row or row['status'] != 'blocked' or not event
                    or event['id'] != expected or event['kind'] not in {'blocked', 'gave_up'}):
                return False
            payload = kb._json_dict(event['payload'])
            if capacity is not None:
                if not capacity_allows(payload, capacity, owner=row['assignee'],
                                       blocked_at=event['created_at'], now=now):
                    return False
                auto_resume = {"trigger": "provider_capacity", "blocked_event_id": event['id'],
                               "provider": capacity['provider'], "account": capacity['account'],
                               "resource": capacity['resource']}
            else:
                from hermes_cli.kanban_recovery import requires_capacity_or_manual_recovery
                if requires_capacity_or_manual_recovery(payload):
                    return False
        resume_status = (
            kb._resume_status_from_events(conn, task_id)
            if kb._task_status(conn, task_id) == "blocked"
            else "ready"
        )
        kb._reclaim_dangling_run(
            conn, task_id, statuses=("blocked", "scheduled"), now=now,
            note="invariant recovery on unblock",
        )
        # Re-gate on parent completion before restoring the source phase.
        landing_status = kb._landing_status_after_parents(conn, task_id)
        new_status = (
            "review"
            if landing_status == "ready" and resume_status == "review"
            else landing_status
        )
        # ``block_kind``/``block_recurrences`` deliberately survive the unblock:
        # resetting them is the amnesia that let cron-unblock <-> re-block loop
        # unbounded; only complete_task clears them. ``consecutive_failures``
        # (the dispatcher's spawn/crash counter) IS reset — a deliberate unblock
        # is a fresh start for the retry budget.
        cur = conn.execute(
            "UPDATE tasks SET status = ?, current_run_id = NULL, "
            "consecutive_failures = 0, last_failure_error = NULL "
            "WHERE id = ? AND status IN ('blocked', 'scheduled')", (new_status, task_id),
        )
        if cur.rowcount != 1:
            return False
        unblocked_payload = (
            {"status": new_status, "resume_status": resume_status}
            if new_status != "ready" or resume_status != "ready"
            else None
        )
        if auto_resume is not None:
            unblocked_payload = dict(unblocked_payload or {}, auto=True)
        kb._append_event(conn, task_id, "unblocked", unblocked_payload)
        if auto_resume is not None:
            kb._append_event(conn, task_id, "auto_resumed", auto_resume)
        return True
