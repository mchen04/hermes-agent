from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_QUIET_KINDS = ("heartbeat",)
_OWN_START_KINDS = frozenset({"claimed", "spawned", "owner_admitted", "worker_bound"})


def legacy_query(task_id: str) -> str:
    return f"work kanban task {task_id}"


def _settings() -> dict:
    from hermes_cli.config import cfg_get, load_config
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    settings = dict(DEFAULT_CONFIG["kanban"]["worker_wake"])
    raw = cfg_get(load_config(), "kanban", "worker_wake", default=None)
    if isinstance(raw, dict):
        settings.update(raw)
    return settings


def _clip(text: Any, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: max(0, limit - 1)].rstrip() + "…"


def _age(now: float, then: Optional[int]) -> str:
    seconds = max(0, int(now - (then or now)))
    if seconds < 120:
        return f"{seconds}s"
    return f"{seconds // 60}m" if seconds < 7200 else f"{seconds // 3600}h"


def _previous_run(conn: Any, task: Any) -> Optional[Any]:
    return conn.execute(
        "SELECT * FROM task_runs WHERE task_id = ? AND ended_at IS NOT NULL AND id IS NOT ? "
        "ORDER BY id DESC LIMIT 1", (task.id, task.current_run_id)).fetchone()


def _run_end_event_id(conn: Any, task_id: str, run: Any) -> int:
    row = conn.execute("SELECT MAX(id) FROM task_events WHERE task_id = ? AND run_id = ?",
                       (task_id, run["id"])).fetchone()
    if row[0] is not None:
        return int(row[0])
    row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM task_events WHERE task_id = ? AND created_at <= ?",
                       (task_id, run["ended_at"])).fetchone()
    return int(row[0])


def _event_detail(payload: dict) -> str:
    for key in ("reason", "summary", "error", "status", "note"):
        if payload.get(key):
            return str(payload[key])
    return ""


def _new_items(conn: Any, task: Any, after_event_id: int, now: float, settings: dict) -> tuple[list[str], int, int]:
    rows = conn.execute(
        "SELECT id, kind, payload, run_id, created_at FROM task_events WHERE task_id = ? AND id > ? "
        f"AND kind NOT IN ({','.join('?' for _ in _QUIET_KINDS)}) ORDER BY id",
        (task.id, after_event_id, *_QUIET_KINDS)).fetchall()
    item_chars = int(settings.get("item_chars") or 400)
    items, n_comments, n_events = [], 0, 0
    for row in rows:
        if row["kind"] in _OWN_START_KINDS and row["run_id"] == task.current_run_id:
            continue
        payload = json.loads(row["payload"]) if row["payload"] else {}
        payload = payload if isinstance(payload, dict) else {}
        if row["kind"] == "commented" and payload.get("comment_id"):
            comment = conn.execute("SELECT author, body FROM task_comments WHERE id = ?",
                                   (payload["comment_id"],)).fetchone()
            if comment is None:
                continue
            n_comments += 1
            origin = f" ({payload['origin']})" if payload.get("origin") else ""
            items.append(f"- comment #{payload['comment_id']} {comment['author']}{origin}, "
                         f"{_age(now, row['created_at'])} ago: {_clip(comment['body'], item_chars)}")
            continue
        n_events += 1
        detail = _event_detail(payload)
        items.append(f"- {row['kind']}, {_age(now, row['created_at'])} ago" + (f": {_clip(detail, item_chars)}" if detail else ""))
    return items, n_comments, n_events


def _cursor(conn: Any, task_id: str) -> dict:
    return {
        "task_id": task_id,
        "database": conn.execute("PRAGMA database_list").fetchone()[2],
        "event_id": conn.execute("SELECT COALESCE(MAX(id), 0) FROM task_events WHERE task_id = ?",
                                 (task_id,)).fetchone()[0],
        "comment_id": conn.execute("SELECT COALESCE(MAX(id), 0) FROM task_comments WHERE task_id = ?",
                                   (task_id,)).fetchone()[0],
    }


def build_worker_wake(conn: Any, task: Any, settings: dict, *, now: Optional[float] = None) -> str:
    previous = _previous_run(conn, task)
    if previous is None or not settings.get("enabled", True):
        return legacy_query(task.id)
    now = time.time() if now is None else now
    items, n_comments, n_events = _new_items(conn, task, _run_end_event_id(conn, task.id, previous), now, settings)
    max_items = int(settings.get("max_items") or 20)
    lines = [f"{legacy_query(task.id)}: {task.title}",
             f"Run {task.current_run_id}. Your previous run {previous['id']} ended "
             f"{previous['outcome'] or previous['status']} {_age(now, previous['ended_at'])} ago."]
    body = (task.body or "").strip()
    if body and len(body) <= int(settings.get("brief_chars") or 6000):
        lines += ["", "Brief (the card body, unchanged unless an edit is listed below):", body]
    elif body:
        lines += ["", f"Brief: {len(body)} chars, too long for this message; read it with kanban_show()."]
    handoff = previous["summary"] or previous["error"]
    if handoff:
        lines += ["", f"Your last handoff (run {previous['id']}):",
                  _clip(handoff, int(settings.get("handoff_chars") or 1500))]
    lines += ["", f"New since run {previous['id']} ended: {n_comments} comment(s), {n_events} event(s)."]
    if len(items) > max_items:
        lines.append(f"({len(items) - max_items} older item(s) omitted; kanban_show() lists them.)")
    lines += items[-max_items:]
    cursor = _cursor(conn, task.id)
    lines += ["", f"Cursor: {json.dumps(cursor)}",
              "kanban_show(cursor=<this cursor>) returns only what came after this message. "
              "kanban_show() with no cursor returns the full card: parent handoffs, attachments, every run "
              "and the comment history."]
    return "\n".join(lines)


def worker_wake_text(db_path: str, task_id: str) -> str:
    """The first message of a card's attempt: the plain wake on a first run, else what changed
    since the previous run. The profile owner submits it as the card turn's text."""
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc

    try:
        settings = _settings()
        with kbc.connect_closing(Path(db_path)) as conn:
            task = kb.get_task(conn, task_id)
            return legacy_query(task_id) if task is None else build_worker_wake(conn, task, settings)
    except Exception:
        logger.warning("kanban worker wake: delta for %s failed; sending the plain wake", task_id, exc_info=True)
        return legacy_query(task_id)
