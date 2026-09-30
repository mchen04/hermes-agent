"""LOCAL-PATCH learn-workers + learn-failed-cron: background review for short-lived runs.

The post-turn review runs in a daemon thread, so a process that exits right after its last
turn kills it. Two such runs get a review that can finish:

* A Kanban worker defers its reviews to exit. At a normal exit it runs one review over the
  whole session when the session did real work, and waits for it (bounded). A signal exit
  (SIGTERM from the dispatcher, Ctrl-C) skips the wait.
* A failed cron agent run starts a review after delivery; the scheduler tears the agent down
  only when that review ends. Successful cron runs keep skipping review.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

DEFAULT_WORKER_REVIEW_WAIT_SECONDS = 180.0
DEFAULT_WORKER_REVIEW_MIN_TOOL_CALLS = 3
# Hard bound on how long a failed cron run's agent stays alive for its review.
FAILED_CRON_REVIEW_MAX_WAIT_SECONDS = 600.0

# The Kanban task whose worker process promised to run its review at exit ("" = none).
_exit_review_task = ""


def defer_reviews_to_exit() -> None:
    """Called by the Kanban worker entry: this process reviews its session once, at exit."""
    global _exit_review_task
    from agent.delegation_context import owned_kanban_task

    _exit_review_task = owned_kanban_task()


def reviews_deferred_to_exit(agent: Any) -> bool:
    """True for the top-level agent of the worker that owns the task and promised an exit review."""
    if not _exit_review_task or getattr(agent, "_delegate_depth", 0) > 0:
        return False
    from agent.delegation_context import owned_kanban_task

    return owned_kanban_task() == _exit_review_task


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _section(name: str) -> dict:
    try:
        from hermes_cli.config import load_config_readonly

        section = (load_config_readonly() or {}).get(name)
    except Exception:
        logger.debug("config read for %s failed", name, exc_info=True)
        return {}
    return section if isinstance(section, dict) else {}


def worker_review_wait_seconds() -> float:
    """``kanban.worker_review_wait_seconds``: how long a worker waits for its exit review (0 = off)."""
    return max(0.0, _number(
        _section("kanban").get("worker_review_wait_seconds"), DEFAULT_WORKER_REVIEW_WAIT_SECONDS))


def worker_review_min_tool_calls() -> int:
    """``kanban.worker_review_min_tool_calls``: work tool calls that make a session worth a review."""
    return max(0, int(_number(
        _section("kanban").get("worker_review_min_tool_calls"), DEFAULT_WORKER_REVIEW_MIN_TOOL_CALLS)))


def failed_cron_review_enabled() -> bool:
    """``cron.review_failed_runs`` (default on)."""
    from utils import is_truthy_value

    return is_truthy_value(_section("cron").get("review_failed_runs"), default=True)


def _live_review_thread(agent: Any) -> Optional[threading.Thread]:
    thread = getattr(agent, "_background_review_thread", None)
    return thread if isinstance(thread, threading.Thread) and thread.is_alive() else None


def wait_for_review(agent: Any, timeout: float) -> bool:
    """Join the agent's running review thread; True when no review is left running."""
    thread = _live_review_thread(agent)
    if thread is None:
        return True
    thread.join(max(0.0, timeout))
    return not thread.is_alive()


def _tool_call_name(call: Any) -> str:
    if isinstance(call, dict):
        fn = call.get("function")
        return str((fn.get("name") if isinstance(fn, dict) else call.get("name")) or "")
    return ""


def work_tool_calls(messages: Iterable[Any]) -> int:
    """Tool calls in the transcript, not counting Kanban board bookkeeping."""
    count = 0
    for message in messages or []:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            if not _tool_call_name(call).startswith("kanban_"):
                count += 1
    return count


def offer_skill_manage(review_agent: Any) -> None:
    """Give a review fork ``skill_manage`` when its one-shot parent had it hidden.

    A ``chat -q`` run (every Kanban worker) drops ``skill_manage`` so the worker does not author
    skills mid-task (agent/oneshot_footprint.py). The review fork exists to maintain skills, and a
    worker's profile keeps its skills for the next worker, so the fork gets the tool back. Only a
    parent with the skills toolset on (``skill_view`` present) qualifies.
    """
    names = getattr(review_agent, "valid_tool_names", None)
    if not isinstance(names, set) or "skill_manage" in names or "skill_view" not in names:
        return
    import model_tools

    schema = next((tool for tool in model_tools.get_tool_definitions(enabled_toolsets=["skills"], quiet_mode=True)
                   if tool.get("function", {}).get("name") == "skill_manage"), None)
    if schema is None:
        return
    review_agent.tools = [*(getattr(review_agent, "tools", None) or []), schema]
    names.add("skill_manage")


def start_review(agent: Any, messages: list, label: str) -> Optional[threading.Thread]:
    """Spawn a memory+skill review over ``messages``; return its thread, or None if none started."""
    names = getattr(agent, "valid_tool_names", None) or set()
    review_memory = "memory" in names and getattr(agent, "_memory_store", None) is not None
    # A one-shot parent hides skill_manage but keeps skill_view; its fork gets skill_manage back.
    review_skills = "skill_manage" in names or "skill_view" in names
    session_id = getattr(agent, "session_id", "?")
    if not (review_memory or review_skills):
        logger.info("%s review skipped for session %s: the run has no memory or skill tools", label, session_id)
        return None
    agent._background_review_thread = None
    agent._spawn_background_review(
        messages_snapshot=list(messages), review_memory=review_memory, review_skills=review_skills)
    thread = getattr(agent, "_background_review_thread", None)
    if isinstance(thread, threading.Thread):
        logger.info("%s review started for session %s (memory=%s skills=%s)",
                    label, session_id, review_memory, review_skills)
        return thread
    return None


def run_worker_exit_review(agent: Any, *, wait_seconds: float, min_tool_calls: int) -> str:
    """Review a finished Kanban worker session and wait for the review, bounded by ``wait_seconds``.

    Returns the outcome: ``off``, ``not_due``, ``not_started``, ``complete`` or ``timed_out``.
    """
    if agent is None or wait_seconds <= 0:
        return "off"
    deadline = time.monotonic() + wait_seconds
    session_id = getattr(agent, "session_id", "?")
    # A review that is already running (started before the deferral) gets the budget first.
    if not wait_for_review(agent, wait_seconds):
        logger.warning("Kanban worker exit: review for session %s still running after %.0fs; exiting",
                       session_id, wait_seconds)
        return "timed_out"
    messages = list(getattr(agent, "_session_messages", None) or [])
    calls = work_tool_calls(messages)
    if not (getattr(agent, "_exit_review_due", False) or (calls and calls >= min_tool_calls)):
        logger.info("Kanban worker exit: no review for session %s (%d work tool calls, minimum %d)",
                    session_id, calls, min_tool_calls)
        return "not_due"
    agent._exit_review_due = False
    thread = start_review(agent, messages, "Kanban worker exit")
    if thread is None:
        return "not_started"
    remaining = max(0.0, deadline - time.monotonic())
    thread.join(remaining)
    if thread.is_alive():
        logger.warning("Kanban worker exit: review for session %s did not finish within %.0fs; exiting",
                       session_id, wait_seconds)
        return "timed_out"
    return "complete"


def start_failed_cron_review(agent: Any, job_id: str) -> Optional[threading.Thread]:
    """LOCAL-PATCH learn-failed-cron: start a review of a failed cron agent run. Never raises."""
    try:
        if agent is None or not failed_cron_review_enabled():
            return None
        messages = list(getattr(agent, "_session_messages", None) or [])
        if not any(isinstance(m, dict) and m.get("role") == "assistant" for m in messages):
            logger.info("Job '%s': failed run has no model output to review", job_id)
            return None
        return start_review(agent, messages, f"Job '{job_id}' failed run")
    except Exception:
        logger.warning("Job '%s': failed-run review could not start", job_id, exc_info=True)
        return None
