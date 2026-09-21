"""LOCAL-PATCH kanban-incremental-read: task updates without repeated startup context."""

_TASK_FIELDS = tuple(
    "id title body assignee status tenant priority workspace_kind workspace_path created_by "
    "created_at started_at completed_at result current_run_id model_override "
    "provider_override completion_contract last_failure_error".split())
_STATUS_FIELDS = tuple("id title assignee status current_run_id result last_failure_error".split())
_RUN_FIELDS = tuple("id profile status outcome summary error metadata started_at ended_at".split())
_COMMENT_FIELDS = ("id", "author", "body", "created_at")
_EVENT_FIELDS = ("id", "kind", "payload", "created_at", "run_id")
_UPDATE_LIMIT = 100


def _fields(obj, names):
    return {name: getattr(obj, name) for name in names}


def _cursor(conn, task_id):
    return {
        "task_id": task_id,
        "database": conn.execute("PRAGMA database_list").fetchone()[2],
        "event_id": conn.execute("SELECT COALESCE(MAX(id),0) FROM task_events WHERE task_id=?", (task_id,)).fetchone()[0],
        "comment_id": conn.execute("SELECT COALESCE(MAX(id),0) FROM task_comments WHERE task_id=?", (task_id,)).fetchone()[0],
    }


def _validate_cursor(cursor, current):
    if not isinstance(cursor, dict) or any(cursor.get(key) != current[key] for key in ("task_id", "database")):
        raise ValueError("cursor belongs to another task or board; omit it for full orientation")
    for key in ("event_id", "comment_id"):
        value = cursor.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= current[key]:
            raise ValueError(f"invalid or stale cursor {key}; omit the cursor for full orientation")


def _read_updates(kb, conn, task, cursor, current):
    _validate_cursor(cursor, current)
    events = [kb.Event.from_row(row) for row in conn.execute(
        "SELECT * FROM task_events WHERE task_id=? AND id>? ORDER BY id LIMIT ?",
        (task.id, cursor["event_id"], _UPDATE_LIMIT + 1),
    )]
    comments = [kb.Comment.from_row(row) for row in conn.execute(
        "SELECT * FROM task_comments WHERE task_id=? AND id>? ORDER BY id LIMIT ?",
        (task.id, cursor["comment_id"], _UPDATE_LIMIT + 1),
    )]
    truncated = {"events": len(events) > _UPDATE_LIMIT, "comments": len(comments) > _UPDATE_LIMIT}
    events, comments = events[:_UPDATE_LIMIT], comments[:_UPDATE_LIMIT]
    # Advance each stream only past returned rows, including same-second bursts.
    current["event_id"] = events[-1].id if events else cursor["event_id"]
    current["comment_id"] = comments[-1].id if comments else cursor["comment_id"]
    run_ids = {event.run_id for event in events if event.run_id is not None}
    if task.current_run_id is not None:
        run_ids.add(task.current_run_id)
    runs = []
    if run_ids:
        marks = ",".join("?" for _ in run_ids)
        runs = [kb.Run.from_row(row) for row in conn.execute(
            f"SELECT * FROM task_runs WHERE task_id=? AND id IN ({marks}) ORDER BY id",
            (task.id, *sorted(run_ids)),
        )]
    return events, comments, runs, truncated


def build_task_read(kb, conn, task, *, cursor=None):
    """Caller holds a read transaction so rows and watermarks share one snapshot."""
    current = _cursor(conn, task.id)
    if cursor is None:
        events = kb.list_events(conn, task.id)
        comments, runs = kb.list_comments(conn, task.id), kb.list_runs(conn, task.id)
        truncated = {"events": len(events) > 50, "comments": False}
        events = events[-50:]
    else:
        events, comments, runs, truncated = _read_updates(kb, conn, task, cursor, current)
    # A changed brief must be read in full; otherwise its original text is already in context.
    full_task = cursor is None or any(event.kind in {"edited", "specified"} for event in events)
    result = {
        "cursor": current,
        "task": _fields(task, _TASK_FIELDS if full_task else _STATUS_FIELDS),
        "parents": kb.parent_ids(conn, task.id),
        "unsatisfied_parents": [{"id": tid, "status": status}
                                for tid, status in kb.unsatisfied_parents(conn, task.id)],
        "children": kb.child_ids(conn, task.id),
        "comments": [_fields(comment, _COMMENT_FIELDS) for comment in comments],
        "events": [_fields(event, _EVENT_FIELDS) for event in events],
        "runs": [_fields(run, _RUN_FIELDS) for run in runs],
        "truncated": truncated,
    }
    if cursor is None:
        result["worker_context"] = kb.build_worker_context(conn, task.id)
    return result
