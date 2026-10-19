from __future__ import annotations

from typing import Any, Optional

VALID_BLOCK_KINDS = {"dependency", "needs_input", "capability", "transient", "capacity", "stop-rule"}

BLOCK_KINDS_WITHOUT_LOOP_COUNT = frozenset({"needs_input", "stop-rule"})

_WAITS_ON = {
    "needs_input": "human",
    "capability": "human",
    "stop-rule": "stop-rule",
    "capacity": "capacity",
    "transient": "retry",
    "dependency": "dependency",
}


def task_waits_on(task: Optional[Any]) -> Optional[str]:
    if task is None:
        return None
    kind = getattr(task, "block_kind", None)
    if task.status == "todo":
        return "dependency" if kind == "dependency" else None
    if task.status not in ("blocked", "triage"):
        return None
    if kind in _WAITS_ON:
        return _WAITS_ON[kind]
    return "repair" if getattr(task, "last_failure_error", None) else "unknown"
