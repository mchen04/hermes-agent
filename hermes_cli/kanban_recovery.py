"""Exact, explicitly authorized capacity recovery; prose is never attribution."""
import json
import re


def authorized_pr_continuation(conn, task_id, comment_id, commented_at):
    """LOCAL-PATCH kanban-pr-continuation: a PR link cannot revoke an answered block."""
    rows = conn.execute(
        "SELECT kind, payload, created_at FROM task_events WHERE task_id = ? "
        "AND kind IN ('unblocked', 'auto_resumed') AND created_at >= ? ORDER BY id DESC",
        (task_id, commented_at),
    ).fetchall()
    has_watermark = legacy_authorized = False
    for row in rows:
        data = json.loads(row["payload"]) if row["payload"] else {}
        if not isinstance(data, dict):
            continue
        watermark = data.get("continuation_after_comment")
        if isinstance(watermark, int) and not isinstance(watermark, bool):
            has_watermark = True
            if watermark >= comment_id:
                return True
            continue
        # Old records have second-granularity timestamps but no comment watermark.
        if row["created_at"] > commented_at and (
            (row["kind"] == "unblocked" and not data.get("auto"))
            or (row["kind"] == "auto_resumed" and data.get("trigger") == "answered")
        ):
            legacy_authorized = True
    return legacy_authorized and not has_watermark


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
