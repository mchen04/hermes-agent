from __future__ import annotations

import os


def _session_env(name: str) -> str:
    try:
        from gateway.session_context import get_session_env
    except ImportError:
        return (os.environ.get(name) or "").strip()
    return (get_session_env(name, "") or "").strip()


def event_origin(task_id: str) -> dict:
    from hermes_cli.profiles import current_profile_name

    worker = (os.environ.get("HERMES_KANBAN_TASK") or "").strip() == task_id
    stamp = {"origin": "worker" if worker else "operator", "actor": current_profile_name() or None}
    platform, chat_id = _session_env("HERMES_SESSION_PLATFORM"), _session_env("HERMES_SESSION_CHAT_ID")
    if not worker and platform and chat_id:
        stamp["actor_chat"] = f"{platform.lower()}:{chat_id}"
    return stamp
