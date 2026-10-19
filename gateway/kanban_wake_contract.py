from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Iterable, Optional

_DISPATCHER_KINDS = frozenset({"crashed", "timed_out", "gave_up"})
_REPLAY_DROP_KINDS = frozenset({"crashed", "timed_out"})
_RESULT_KINDS = frozenset({"completed", "review_requested"})
_BLOCK_KINDS = frozenset({"blocked", "block_loop_detected"})
_HUMAN_BLOCK_KINDS = frozenset({"needs_input", "capability"})
_QUIET_KINDS = ("heartbeat",)
_SELF_DROP_KINDS = frozenset({"blocked", "completed", "review_requested", "block_loop_detected"})
_PR_KEYS = ("published_pr", "pr_url", "pr")
_HEAD_KEYS = ("head_sha", "head", "commit")
_MERGE_KEYS = ("merge_commit", "merge_sha")


def wake_settings(kanban_cfg: Optional[dict] = None) -> dict:
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    settings = dict(DEFAULT_CONFIG["kanban"]["wake"])
    raw = (kanban_cfg or {}).get("wake") if isinstance(kanban_cfg, dict) else None
    if isinstance(raw, dict):
        settings.update(raw)
    return settings


def _json(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def clip(text: Any, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: max(0, limit - 1)].rstrip() + "…"


def age_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 120:
        return f"{seconds}s"
    if seconds < 7200:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"


def _runs(conn: Any, run_ids: Iterable[int]) -> dict:
    out = {}
    for run_id in sorted(set(run_ids)):
        row = conn.execute(
            "SELECT id, claim_lock, worker_pid, started_at, ended_at, summary, metadata FROM task_runs WHERE id = ?",
            (run_id,)).fetchone()
        if row is not None:
            out[run_id] = {key: row[key] for key in row.keys()}
    return out


def _publish_state(conn: Any, task_id: str, upto_event_id: int) -> dict:
    state: dict = {}
    row = conn.execute(
        "SELECT payload FROM task_events WHERE task_id = ? AND kind = 'pr_acceptance' AND id <= ? "
        "ORDER BY id DESC LIMIT 1", (task_id, upto_event_id)).fetchone()
    if row is not None:
        acceptance = _json(row["payload"])
        state.update({k: v for k, v in (("pr", acceptance.get("pr_url")), ("head_sha", acceptance.get("head_sha")),
                                       ("pr_state", acceptance.get("detail"))) if v})
    run = conn.execute(
        "SELECT metadata FROM task_runs WHERE task_id = ? AND outcome = 'completed' AND metadata IS NOT NULL "
        "AND ended_at <= (SELECT created_at FROM task_events WHERE id = ?) ORDER BY id DESC LIMIT 1",
        (task_id, upto_event_id)).fetchone()
    meta = _json(run["metadata"]) if run is not None else {}
    for target, keys in (("pr", _PR_KEYS), ("head_sha", _HEAD_KEYS), ("merge_sha", _MERGE_KEYS)):
        value = next((meta[k] for k in keys if isinstance(meta.get(k), str) and meta[k]), None)
        if value and target not in state:
            state[target] = value
    if isinstance(meta.get("pr_state"), str) and "pr_state" not in state:
        state["pr_state"] = meta["pr_state"]
    deploy = meta.get("deployment")
    if isinstance(deploy, dict):
        state["deploy"] = " ".join(str(deploy[k]) for k in ("state", "status", "url") if deploy.get(k))
    elif isinstance(deploy, str) and deploy:
        state["deploy"] = deploy
    return state


def collect_context(conn: Any, task_id: str, old_cursor: int, cursor: int, events: list) -> dict:
    rows = conn.execute(
        "SELECT id, kind, payload FROM task_events WHERE task_id = ? AND id > ? AND id <= ? "
        f"AND kind NOT IN ({','.join('?' for _ in _QUIET_KINDS)}) ORDER BY id",
        (task_id, old_cursor, cursor, *_QUIET_KINDS)).fetchall()
    between, comments = [], []
    for row in rows:
        payload = _json(row["payload"])
        if row["kind"] == "commented":
            comments.append({"id": payload.get("comment_id"), "author": payload.get("author"),
                             "origin": payload.get("origin")})
        else:
            between.append({"id": row["id"], "kind": row["kind"], "reason": payload.get("reason")})
    mark = conn.execute(
        "SELECT payload FROM task_events WHERE task_id = ? AND kind = 'commented' AND id <= ? ORDER BY id DESC LIMIT 1",
        (task_id, old_cursor)).fetchone()
    comment_mark = int(_json(mark["payload"]).get("comment_id") or 0) if mark is not None else 0
    database = conn.execute("PRAGMA database_list").fetchone()[2]
    return {
        "runs": _runs(conn, (ev.run_id for ev in events if ev.run_id is not None)),
        "between": between,
        "comments": comments,
        "publish": _publish_state(conn, task_id, cursor),
        "cursor": {"task_id": task_id, "database": database, "event_id": int(old_cursor), "comment_id": comment_mark},
    }


def caused_by(ev: Any, context: dict, *, now: float, settings: dict) -> str:
    if ev.kind in _DISPATCHER_KINDS:
        late = now - ev.created_at > float(settings.get("replay_max_age_seconds") or 0)
        return "restart replay" if late else "dispatcher"
    origin = (ev.payload or {}).get("origin")
    if origin in ("worker", "operator"):
        return origin
    run = context.get("runs", {}).get(ev.run_id)
    if run is None:
        return "operator"
    synthesized = not run.get("claim_lock") and run.get("started_at") == run.get("ended_at")
    return "operator" if synthesized else "worker"


def subscriber_chat(sub: dict) -> str:
    return f"{str(sub.get('platform') or '').lower()}:{sub.get('chat_id') or ''}"


def drop_reason(ev: Any, *, now: float, settings: dict, subscriber: str) -> Optional[str]:
    replay_age = float(settings.get("replay_max_age_seconds") or 0)
    if ev.kind in _REPLAY_DROP_KINDS and replay_age and now - ev.created_at > replay_age:
        return "crash replay"
    payload = ev.payload or {}
    if (settings.get("drop_self_caused") and ev.kind in _SELF_DROP_KINDS
            and payload.get("origin") == "operator" and payload.get("actor_chat") == subscriber):
        return "self-caused"
    return None


def is_evidence(path: str, settings: dict) -> bool:
    suffixes = tuple(str(s).lower() for s in settings.get("evidence_suffixes") or ())
    return bool(suffixes) and os.path.basename(str(path)).lower().endswith(suffixes)


def split_deliverables(paths: Iterable[str], settings: dict) -> tuple[list[str], list[str]]:
    deliver, evidence = [], []
    for path in paths:
        (evidence if is_evidence(path, settings) else deliver).append(path)
    return deliver, evidence


def result_summary(ev: Any, context: dict, settings: dict) -> Optional[str]:
    run = context.get("runs", {}).get(ev.run_id) or {}
    text = run.get("summary") or (ev.payload or {}).get("summary")
    return clip(text, int(settings.get("result_chars") or 500)) if text else None


def question_of(reason: str) -> Optional[str]:
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(str(reason or "").split()))
    asked = [sentence for sentence in sentences if sentence.endswith("?")]
    return " ".join(asked) or None


def _block_lines(kind: str, reason: str, reason_chars: int) -> list[str]:
    lines = [f"block: {kind} — {clip(reason, reason_chars)}"]
    if kind not in _HUMAN_BLOCK_KINDS:
        return [*lines, "needs_human: no"]
    question = question_of(reason)
    return [*lines, f"needs_human: yes — {clip(question, reason_chars)}" if question
            else "needs_human: yes — see the block reason"]


def _event_line(ev: Any, context: dict, *, now: float, settings: dict) -> list[str]:
    payload = ev.payload or {}
    reason_chars = int(settings.get("reason_chars") or 300)
    cause = caused_by(ev, context, now=now, settings=settings)
    if cause == "operator" and payload.get("actor"):
        cause = f"operator ({payload['actor']})"
    lines = [f"event: {ev.kind} · caused_by {cause} · {age_text(now - ev.created_at)} ago"]
    if ev.kind in _BLOCK_KINDS:
        lines += _block_lines(payload.get("kind") or "untyped", payload.get("reason") or "no reason given",
                              reason_chars)
    elif ev.kind == "gave_up":
        failures = payload.get("failures")
        head = f"gave_up after {failures} failures" if failures else "gave_up"
        lines += [f"block: {head} — {clip(payload.get('error') or 'repeated failures', reason_chars)}",
                  "needs_human: no"]
    elif ev.kind in _DISPATCHER_KINDS:
        lines.append("needs_human: no")
    return lines


def _new_since_line(context: dict, shown_ids: set) -> Optional[str]:
    by_author: dict = {}
    for comment in context.get("comments", []):
        if comment.get("id"):
            by_author.setdefault(comment.get("author") or "unknown", []).append(f"#{comment['id']}")
    kinds = [e["kind"] for e in context.get("between", []) if e["id"] not in shown_ids]
    parts = []
    if by_author:
        parts.append("comments " + "; ".join(f"{author} {' '.join(ids)}" for author, ids in by_author.items()))
    if kinds:
        parts.append("events " + ", ".join(kinds))
    return "new since last wake: " + "; ".join(parts) if parts else None


def _publish_line(context: dict) -> Optional[str]:
    state = context.get("publish") or {}
    parts = []
    if state.get("pr"):
        parts.append(f"PR {state['pr']}" + (f" {state['pr_state']}" if state.get("pr_state") else ""))
    if state.get("head_sha"):
        parts.append(f"head {str(state['head_sha'])[:12]}")
    if state.get("merge_sha"):
        parts.append(f"merge {str(state['merge_sha'])[:12]}")
    if state.get("deploy"):
        parts.append(f"deploy {clip(state['deploy'], 120)}")
    return "publish: " + " · ".join(parts) if parts else None


def contract_lines(events: list, context: dict, *, settings: dict, now: Optional[float] = None) -> list[str]:
    now = time.time() if now is None else now
    lines: list[str] = []
    for ev in events:
        lines.extend(_event_line(ev, context, now=now, settings=settings))
    evidence = []
    for ev in events:
        if ev.kind in _RESULT_KINDS:
            evidence += [p for p in (ev.payload or {}).get("artifacts") or [] if is_evidence(p, settings)]
    if evidence:
        lines.append("evidence (paths, not uploaded): " + ", ".join(dict.fromkeys(evidence)))
    for line in (_publish_line(context), _new_since_line(context, {ev.id for ev in events})):
        if line:
            lines.append(line)
    if context.get("cursor"):
        task_id = context["cursor"]["task_id"]
        lines.append(f'delta read: kanban_show(task_id="{task_id}", cursor={json.dumps(context["cursor"])})')
    return lines
