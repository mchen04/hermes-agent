from __future__ import annotations

import os
import sqlite3
from typing import Optional

from agent.delegation_context import is_delegated_child_process_context
from hermes_cli import kanban_db as kb
from hermes_cli.kanban_event_origin import event_origin
from hermes_cli.kanban_pr_acceptance import validate_contract
from hermes_cli.kanban_pr_resume import _authorization, _operator_authorization


def change_completion_contract(
    conn: sqlite3.Connection, task_id: str, contract: Optional[str], *, reason: str,
    authorization_comment_id: int, board: Optional[str] = None,
) -> bool:
    if (os.environ.get("HERMES_KANBAN_TASK") or "").strip() or is_delegated_child_process_context():
        raise PermissionError("Kanban workers cannot change a completion contract; the operator changes it")
    contract = validate_contract(contract)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("a non-empty reason is required to change a completion contract")
    if type(authorization_comment_id) is not int or authorization_comment_id <= 0:
        raise ValueError("a positive authorization comment id is required to change a completion contract")
    with kb.write_txn(conn):
        row = conn.execute(
            "SELECT assignee, completion_contract FROM tasks WHERE id=?", (task_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown task {task_id}")
        auth = _authorization(conn, task_id, authorization_comment_id)
        if auth is None or not _operator_authorization(conn, task_id, auth, {"assignee": row["assignee"]}):
            raise PermissionError(
                f"comment {authorization_comment_id} is not an operator authorization on {task_id}; "
                "worker comments and other cards' comments are refused")
        previous = row["completion_contract"]
        if (previous or validate_contract(None)) == contract:
            return False
        conn.execute("UPDATE tasks SET completion_contract=? WHERE id=?", (contract, task_id))
        kb._append_event(conn, task_id, "edited", {
            "fields": ["completion_contract"], "from": previous, "to": contract,
            "reason": reason.strip(), "authorization": auth, **event_origin(task_id),
        })
    kb.notify_task_updated(conn, task_id, ["completion_contract"], board=board)
    return True
