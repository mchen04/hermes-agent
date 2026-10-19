from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any, Optional

_DISPATCH_EVENT_KINDS = ("spawned", "respawn_guarded", "skipped_nonspawnable")
_PR_GUARDS = ("active_pr", "pr_state_unknown")


def last_event_id(conn: Any, task_id: str) -> int:
    row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM task_events WHERE task_id = ?", (task_id,)).fetchone()
    return int(row[0])


def unblock_wait_seconds() -> float:
    from hermes_cli.config import cfg_get, load_config

    try:
        return max(0.0, float(cfg_get(load_config(), "kanban", "unblock_wait_seconds", default=0) or 0))
    except (TypeError, ValueError):
        return 0.0


def _not_spawned(outcome: str, **detail: Any) -> dict:
    return {"outcome": outcome, "worker_spawned": False, **detail}


def _verdict_from_result(conn: Any, task: Any, res: Any) -> dict:
    from hermes_cli import kanban_db_dispatch as kbd

    if res.respawn_guarded:
        reason = res.respawn_guarded[0][1]
        guard, detail = kbd.respawn_guard_verdict(conn, task.id) if reason in _PR_GUARDS else (reason, {})
        return _not_spawned("respawn_guarded", reason=reason, **(detail if guard == reason else {}))
    if res.skipped_nonspawnable:
        return _not_spawned("skipped_nonspawnable", assignee=task.assignee)
    if res.skipped_unassigned:
        return _not_spawned("skipped_unassigned")
    if res.skipped_per_profile_capped:
        return _not_spawned("per_profile_capped", assignee=task.assignee, running=res.skipped_per_profile_capped[0][2])
    if res.memory_pressure == "critical":
        return _not_spawned("memory_pressure")
    if res.spawned:
        return _not_spawned("eligible", note="the dispatcher spawns it on its next tick")
    return _not_spawned("at_capacity")


def dispatcher_verdict(conn: Any, task_id: str, *, board: Optional[str] = None) -> dict:
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_dispatch as kbd
    from hermes_cli import kanban_ops

    task = kb.get_task(conn, task_id)
    if task is None:
        return _not_spawned("unknown_task")
    if task.status == "running":
        return {"outcome": "spawned", "worker_spawned": True, "run_id": task.current_run_id}
    if task.status == "todo":
        return _not_spawned("waits_on_parents", parents=[pid for pid, _ in kb.unsatisfied_parents(conn, task_id)])
    if task.status != "ready":
        return _not_spawned("not_dispatchable", status=task.status)
    _, per_profile, max_in_progress, max_spawn = kanban_ops._dispatch_settings(SimpleNamespace(max=None))
    res = kbd.dispatch_task(conn, task_id, max_spawn=max_spawn, max_in_progress=max_in_progress,
                            max_in_progress_per_profile=per_profile, board=board, dry_run=True)
    return _verdict_from_result(conn, task, res)


def _observed(conn: Any, task_id: str, since_event_id: int) -> Optional[dict]:
    marks = ",".join("?" for _ in _DISPATCH_EVENT_KINDS)
    row = conn.execute(
        f"SELECT kind, payload, run_id FROM task_events WHERE task_id = ? AND id > ? AND kind IN ({marks}) "
        "ORDER BY id DESC LIMIT 1", (task_id, since_event_id, *_DISPATCH_EVENT_KINDS)).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload"]) if row["payload"] else {}
    except (TypeError, ValueError):
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    if row["kind"] == "spawned":
        return {"outcome": "spawned", "worker_spawned": True, "run_id": row["run_id"], "pid": payload.get("pid")}
    return _not_spawned(row["kind"], **payload)


def unblock_outcome(conn: Any, task_id: str, *, since_event_id: int, wait_seconds: float = 0.0,
                    board: Optional[str] = None) -> dict:
    observed = _observed(conn, task_id, since_event_id)
    if observed is not None:
        return {**observed, "source": "dispatcher"}
    verdict = dispatcher_verdict(conn, task_id, board=board)
    started = time.monotonic()
    while verdict["outcome"] == "eligible" and time.monotonic() - started < wait_seconds:
        time.sleep(min(1.0, wait_seconds))
        observed = _observed(conn, task_id, since_event_id)
        if observed is not None:
            return {**observed, "source": "dispatcher", "waited_seconds": round(time.monotonic() - started)}
    if verdict["outcome"] == "eligible" and wait_seconds:
        verdict = {**verdict, "waited_seconds": round(time.monotonic() - started)}
    return {**verdict, "source": "dispatcher_check"}


def describe(outcome: dict) -> str:
    detail = {k: v for k, v in outcome.items() if k not in ("outcome", "worker_spawned", "source", "note")}
    head = f"{outcome['outcome']} {detail}" if detail else outcome["outcome"]
    return f"{head}: worker spawned" if outcome.get("worker_spawned") else f"{head}: no worker spawned"
