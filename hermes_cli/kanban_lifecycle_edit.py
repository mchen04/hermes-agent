from __future__ import annotations

import os
import sqlite3
from typing import Optional

from agent.delegation_context import is_delegated_child_process_context
from hermes_cli import kanban_db as kb
from hermes_cli.kanban_event_origin import event_origin
from hermes_cli.kanban_pr_resume import _authorization, _operator_authorization


def change_lifecycle(
    conn: sqlite3.Connection, task_id: str, *, goal_mode: Optional[bool] = None,
    clear_stale_block: bool = False, reason: str, authorization_comment_id: int,
    board: Optional[str] = None,
) -> list[str]:
    if (os.environ.get("HERMES_KANBAN_TASK") or "").strip() or is_delegated_child_process_context():
        raise PermissionError("Kanban workers cannot change a card's goal mode or block record; "
                              "the operator changes it")
    if goal_mode is None and not clear_stale_block:
        raise ValueError("nothing to change: give goal_mode or clear_stale_block")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("a non-empty reason is required to change a card's lifecycle")
    if type(authorization_comment_id) is not int or authorization_comment_id <= 0:
        raise ValueError("a positive authorization comment id is required to change a card's lifecycle")
    with kb.write_txn(conn):
        task = kb.get_task(conn, task_id)
        if task is None:
            raise ValueError(f"unknown task {task_id}")
        auth = _authorization(conn, task_id, authorization_comment_id)
        if auth is None or not _operator_authorization(conn, task_id, auth, {"assignee": task.assignee}):
            raise PermissionError(
                f"comment {authorization_comment_id} is not an operator authorization on {task_id}; "
                "worker comments and other cards' comments are refused")
        if task.status in ("done", "archived"):
            raise ValueError(f"{task_id} is {task.status}; its lifecycle is over")
        payload: dict = {"status": task.status}
        fields: list[str] = []
        if goal_mode is not None and task.goal_mode != goal_mode:
            fields.append("goal_mode")
            payload["goal_mode"] = {"from": task.goal_mode, "to": goal_mode}
        if clear_stale_block and (task.block_kind is not None or task.block_recurrences):
            waits_on = kb.task_waits_on(task)
            if waits_on is not None:
                raise ValueError(f"{task_id} is {task.status} and still waits on {waits_on}; "
                                 "unblock it instead of clearing its block record")
            blocked = conn.execute(
                "SELECT id FROM task_events WHERE task_id=? AND kind IN ('blocked', 'block_loop_detected') "
                "ORDER BY id DESC LIMIT 1", (task_id,),
            ).fetchone()
            fields += ["block_kind", "block_recurrences"]
            payload["cleared_block"] = {"kind": task.block_kind, "recurrences": task.block_recurrences,
                                        "blocked_event_id": blocked["id"] if blocked else None}
        if not fields:
            return []
        conn.execute(
            "UPDATE tasks SET goal_mode=?, block_kind=?, block_recurrences=? WHERE id=?",
            (int(goal_mode if "goal_mode" in fields else task.goal_mode),
             None if "block_kind" in fields else task.block_kind,
             0 if "block_kind" in fields else task.block_recurrences, task_id),
        )
        kb._append_event(conn, task_id, "edited", {
            "fields": fields, **payload, "reason": reason.strip(), "authorization": auth,
            **event_origin(task_id),
        })
    kb.notify_task_updated(conn, task_id, fields, board=board)
    return fields
