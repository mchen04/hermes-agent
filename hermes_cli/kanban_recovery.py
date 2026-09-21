"""Exact, explicitly authorized capacity recovery; prose is never attribution."""
import json
import math
import re
import time

IDENTITY = ('provider', 'account', 'resource')

GOAL_WAIT_MESSAGE = (
    "Goal-mode workers keep ordinary machine/build/coding waits in the same session. "
    "Keep the existing coding owner and claim; wait on its process or completion notification. "
    "Use needs_input for a person's decision or dependency for an unfinished parent task. "
    "A transient block would discard this supervisor and start another one."
)


def authorized_pr_continuation(conn, task_id, comment_id, commented_at):
    """LOCAL-PATCH kanban-pr-continuation: a PR link cannot revoke an answered block."""
    rows = conn.execute(
        "SELECT kind, payload, created_at FROM task_events WHERE task_id = ? "
        "AND kind IN ('unblocked', 'auto_resumed') AND created_at >= ? ORDER BY id DESC",
        (task_id, commented_at),
    ).fetchall()
    for row in rows:
        data = json.loads(row["payload"]) if row["payload"] else {}
        if not isinstance(data, dict):
            continue
        watermark = data.get("continuation_after_comment")
        if isinstance(watermark, int) and not isinstance(watermark, bool):
            if watermark >= comment_id:
                return True
            continue
        # Old records have second-granularity timestamps but no comment watermark.
        if row["created_at"] > commented_at and (
            (row["kind"] == "unblocked" and not data.get("auto"))
            or (row["kind"] == "auto_resumed" and data.get("trigger") == "answered")
        ):
            return True
    return False


def guard_goal_transient_block(row, *, kind, force=False):
    """LOCAL-PATCH kanban-continuity: timers must not replace a live goal supervisor."""
    if row["goal_mode"] and kind == "transient" and not force:
        from hermes_cli.kanban_db import BlockRejected

        raise BlockRejected(GOAL_WAIT_MESSAGE)


def recovery_dependency(payload):
    if not isinstance(payload, dict):
        return None
    reason = payload.get('reason') or payload.get('error') or ''
    if isinstance(reason, str) and re.search(r'\b(?:STOP|HOLD)\b', reason, re.I):
        return None
    dependency = payload.get('recovery')
    if dependency is None and isinstance(reason, str):
        try:
            dependency = json.loads(reason).get('recovery')
        except (ValueError, AttributeError):
            return None
    if not isinstance(dependency, dict) or dependency.get('authorized') is not True:
        return None
    if any(not isinstance(dependency.get(k), str) or not dependency[k].strip()
           for k in (*IDENTITY, 'owner')):
        return None
    deadline = dependency.get('retry_not_before')
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
        return None
    return dependency


def capacity_allows(payload, capacity, *, owner, blocked_at, now=None):
    now = time.time() if now is None else now
    dep = recovery_dependency(payload)
    if dep is None or dep['owner'] != owner or not isinstance(capacity, dict):
        return False
    if capacity.get('available') is not True or any(dep[k] != capacity.get(k) for k in IDENTITY):
        return False
    checked = capacity.get('checked_at')
    if not isinstance(checked, (int, float)) or isinstance(checked, bool) or not 0 <= now - checked <= 60:
        return False
    delay = payload.get('resume_after', 3600)
    if not isinstance(delay, (int, float)) or isinstance(delay, bool) or not math.isfinite(delay):
        return False
    return now >= max(dep['retry_not_before'], blocked_at + max(3600, delay))


def requires_capacity_or_manual_recovery(payload):
    """Automatic timers/comments are not capacity evidence or permission to release a HOLD."""
    reason = payload.get('reason') or payload.get('error') or ''
    if isinstance(reason, str):
        # STOP/HOLD are operator markers and must be uppercase: prose like "not the old preflight hold"
        # is not a hold (LOCAL-PATCH kanban-auto-loop 2026-09-16, t_19069c58 sat parked for hours on it).
        if re.search(r'\b(?:STOP|HOLD)\b', reason):
            return True
        if re.search(r'\b(?:quota|(?:capacity|credits?)[-\s]+(?:exhausted|exceeded|unavailable|depleted|limit)|(?:insufficient|out[-\s]+of|no)[-\s]+credits?|usage[-\s]+limit|'
                     r'rate[-\s]+limit(?:ed|ing)?|(?:HTTP\s*)?429|Too\s+Many\s+Requests)\b', reason, re.I):
            return True
        try:
            detail = json.loads(reason)
        except ValueError:
            # Malformed structured recovery must not fall through to a timer retry.
            return bool(re.search(r'"(?:recovery|retry_not_before)"\s*:', reason))
    else:
        detail = reason
    keys = {'recovery', 'retry_not_before', 'provider', 'account', 'resource'}
    return bool(keys.intersection(payload) or (isinstance(detail, dict) and keys.intersection(detail)))
