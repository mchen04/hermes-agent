"""Operator-recorded authorization to continue an existing, open GitHub PR.

Normal dispatch never interprets comment prose. This receipt lifts only active_pr.
GitHub reads use the invoking operator's login, as stale-PR recovery does.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_dispatch as dispatch
from hermes_cli import kanban_pr_acceptance as acceptance

AUTHORIZED_PR_RESUME_EVENT = "authorized_pr_resume"
# CLI falls back to user, dashboard to dashboard; default is the operator profile.
# Worker tools persist their profile name and cannot supply an author override.
_OPERATOR_AUTHORS = frozenset({"user", "dashboard", "default"})
_OWNER_FIELDS = ("claim_lock", "claim_expires", "current_run_id", "worker_pid")


@dataclass
class AuthorizedPrResume:
    status: str
    task_id: str
    detail: str = ""
    authorization: dict = field(default_factory=dict)
    scope: dict = field(default_factory=dict)
    comments: list = field(default_factory=list)
    prs: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in {"recorded", "already_recorded", "verified"}


def _sha(value) -> str:
    return hashlib.sha256((kb._lossy_text(value) or "").encode("utf-8")).hexdigest()


def _scope(conn, task_id: str) -> dict | None:
    row = conn.execute(
        "SELECT status, assignee, completion_contract, body, branch_name, "
        "claim_lock, claim_expires, current_run_id, worker_pid, worker_started_at "
        "FROM tasks WHERE id=?", (task_id,),
    ).fetchone()
    if row is None:
        return None
    scope = dict(row)
    scope["body_sha256"] = _sha(scope.pop("body"))
    runs = conn.execute(
        "SELECT MAX(id), COUNT(CASE WHEN ended_at IS NULL OR status='running' THEN 1 END), "
        "COUNT(worker_pid) FROM task_runs WHERE task_id=?", (task_id,),
    ).fetchone()
    scope.update(last_run_id=runs[0], active_runs=runs[1], retained_worker_runs=runs[2],
                 fingerprint_run_id=None, ownership_event_id=None)
    if scope["worker_started_at"] is not None:
        # Block, review, schedule, and completion clear worker_pid but keep the task row's
        # fingerprint. Name the run whose spawn recorded it; anything else stays unexplained.
        spawn = conn.execute(
            "SELECT run_id, payload FROM task_events WHERE task_id=? "
            "AND kind IN ('spawned', 'worker_registered') ORDER BY id DESC LIMIT 1", (task_id,),
        ).fetchone()
        if spawn is not None and kb._json_dict(spawn["payload"]).get("started_at") == scope["worker_started_at"]:
            scope["fingerprint_run_id"] = spawn["run_id"]
    for event in conn.execute(
        "SELECT id, kind, payload FROM task_events WHERE task_id=? "
        "AND kind IN ('assigned', 'claimed', 'status') ORDER BY id DESC", (task_id,),
    ):
        if event["kind"] == "assigned":
            data = kb._json_dict(event["payload"])
            if "from" in data and data["from"] == data.get("assignee"):
                continue
        scope["ownership_event_id"] = event["id"]
        break
    return scope


def _ready(scope: dict | None) -> bool:
    if not (scope and scope["status"] == "ready" and scope["assignee"]
            and not scope["active_runs"] and all(scope[key] is None for key in _OWNER_FIELDS)):
        return False
    # A leftover fingerprint is history only when it belongs to the latest run, that run has
    # ended, and no run still retains a worker PID for the terminal-worker reaper.
    return scope["worker_started_at"] is None or bool(
        scope["fingerprint_run_id"] is not None and scope["fingerprint_run_id"] == scope["last_run_id"]
        and not scope["retained_worker_runs"])


def _authorization(conn, task_id: str, comment_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, author, body, created_at FROM task_comments WHERE task_id=? AND id=?",
        (task_id, comment_id),
    ).fetchone()
    if row is None or not (kb._lossy_text(row["body"]) or "").strip():
        return None
    origin = "legacy"
    for event in conn.execute(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='commented' ORDER BY id DESC", (task_id,),
    ):
        data = kb._json_dict(event["payload"])
        if data.get("comment_id") == row["id"]:
            origin = data.get("origin")
            break
    return {"id": row["id"], "author": row["author"], "sha256": _sha(row["body"]),
            "created_at": row["created_at"], "origin": origin}


def _operator_authorization(conn, task_id: str, auth: dict, scope: dict) -> bool:
    author = auth["author"]
    return bool(auth["origin"] in ("legacy", "operator") and author in _OPERATOR_AUTHORS
                and author != scope["assignee"] and not conn.execute(
        "SELECT 1 FROM task_runs WHERE task_id=? AND profile=? LIMIT 1", (task_id, author),
    ).fetchone())


def _later(auth: dict, comments: list[dict]) -> bool:
    # Separate tables do not supply a common sequence: same-second ties fail closed.
    return bool(comments and all(auth["created_at"] > c["created_at"] and auth["id"] > c["id"]
                                 for c in comments))


def authorized_pr_resume_covers(conn, task_id: str, comments: list[dict], *,
                                authorization_comment_id: int | None = None) -> bool:
    rows = conn.execute(
        "SELECT payload FROM task_events WHERE task_id=? AND kind=?",
        (task_id, AUTHORIZED_PR_RESUME_EVENT),
    ).fetchall()
    if not rows:
        return False
    scope = _scope(conn, task_id)
    if not _ready(scope):
        return False
    for row in rows:
        data = kb._json_dict(row["payload"])
        recorded_auth = data.get("authorization")
        recorded = data.get("comments")
        if data.get("scope") != scope or not isinstance(recorded_auth, dict) or not isinstance(recorded, list):
            continue
        comment_id = recorded_auth.get("id")
        if type(comment_id) is not int:
            continue
        if authorization_comment_id is not None and comment_id != authorization_comment_id:
            continue
        auth = _authorization(conn, task_id, comment_id)
        if (auth != recorded_auth or not _operator_authorization(conn, task_id, auth, scope)
                or not _later(auth, comments)):
            continue
        if all(comment in recorded for comment in comments):
            return True
    return False


def _github_pr_evidence(url: str) -> dict:
    match = acceptance._PR.fullmatch(url)
    if not match:
        raise ValueError("Malformed PR URL")
    repo, number = match[1], int(match[2])
    data = acceptance._api(f"repos/{repo}/pulls/{number}")
    if (not isinstance(data, dict) or data.get("html_url") != url or data.get("number") != number
            or data.get("state") not in {"open", "closed"}
            or data["base"]["repo"]["full_name"] != repo
            or not re.fullmatch(r"[0-9a-f]{40}", data["head"]["sha"])
            or not isinstance(data["head"]["ref"], str) or not data["head"]["ref"].strip()
            or type(data.get("draft")) is not bool
            or (data["state"] == "open" and (data.get("merged_at") or data.get("closed_at")))):
        raise ValueError("Unknown or incomplete PR evidence")
    state = "merged" if data.get("merged_at") else data["state"]
    return {"url": url, "repository": repo, "number": number, "state": state,
            "head_sha": data["head"]["sha"], "head_ref": data["head"]["ref"],
            "draft": data["draft"], "merged_at": data.get("merged_at"), "closed_at": data.get("closed_at")}


def recover_authorized_pr_resume(conn, task_id: str, authorization_comment_id: int, *,
                                 actor: str, reason: str, dry_run: bool = False,
                                 caller_task_id: str | None = None) -> AuthorizedPrResume:
    """Verify a named operator authorization, then record its bounded active_pr exception.

    The operator must select an explicit resume directive, not a worker's claim of permission.
    No PR check here grants completion, merge, or publication approval.
    """
    result = AuthorizedPrResume("refused", task_id)

    def refuse(status, detail):
        result.status, result.detail = status, detail
        return result

    if not isinstance(task_id, str) or not task_id.strip():
        return refuse("unknown_task", "An exact target task ID is required")
    if task_id == caller_task_id or task_id == os.environ.get("HERMES_KANBAN_TASK"):
        return refuse("own_task", "Name a different target card explicitly")
    if type(authorization_comment_id) is not int or authorization_comment_id <= 0:
        return refuse("invalid_authorization", "A positive authorization comment ID is required")
    if not isinstance(reason, str) or not reason.strip() or not isinstance(actor, str) or not actor.strip():
        return refuse("invalid_reason", "A non-empty operator reason and actor are required")
    with kb.write_txn(conn):
        scope = _scope(conn, task_id)
        if scope is None:
            return refuse("unknown_task", "Target card does not exist")
        if not _ready(scope):
            return refuse("not_ready", "Target must be ready, assigned, and completely unowned")
        auth = _authorization(conn, task_id, authorization_comment_id)
        if auth is None or not _operator_authorization(conn, task_id, auth, scope):
            return refuse("invalid_authorization", "Select a non-empty operator comment on this target; worker claims are refused")
        snapshot = dispatch._guarded_pr_comments(conn, task_id, int(time.time()))
        if not snapshot:
            return refuse("no_pr_guard", "No in-window guarding PR comments exist")
        if not _later(auth, snapshot):
            return refuse("authorization_not_later", "Authorization must follow every guarding PR comment; same-second ties refuse")
        contract = scope["completion_contract"]
        if not (acceptance._REPO.fullmatch(contract or "") or acceptance._PR.fullmatch(contract or "")):
            return refuse("invalid_contract", "Target must declare a repository or exact PR completion contract")
    result.authorization, result.scope, result.comments = auth, scope, snapshot
    for url in sorted({url for comment in snapshot for url in comment["urls"]}):
        try:
            result.prs.append(_github_pr_evidence(url))
        except Exception as exc:
            # External errors can contain tokens or command stderr. Do not persist them.
            return refuse("pr_lookup_failed", f"GitHub evidence unavailable for {url} ({type(exc).__name__})")
    opened = [pr for pr in result.prs if pr["state"] == "open"]
    if len(opened) != 1:
        return refuse("ambiguous_open_pr", "Exactly one guarded PR must be open; closed references may coexist")
    pr = opened[0]
    if contract not in {pr["url"], pr["repository"]}:
        return refuse("contract_mismatch", "Open PR does not match the target completion contract")
    if scope["branch_name"] and scope["branch_name"] != pr["head_ref"]:
        return refuse("branch_mismatch", "Open PR does not match the target's existing branch")
    # Recheck even for dry runs. No SQLite transaction spans a network call.
    with kb.write_txn(conn):
        current = _scope(conn, task_id)
        if current != scope or not _ready(current):
            return refuse("task_changed", "Target ownership, state, or contract changed during GitHub checks")
        if _authorization(conn, task_id, authorization_comment_id) != auth:
            return refuse("authorization_changed", "Authorization changed during GitHub checks")
        if dispatch._guarded_pr_comments(conn, task_id, int(time.time())) != snapshot:
            return refuse("comments_changed", "Guarding PR comments changed during GitHub checks")
        if dry_run:
            result.status, result.detail = "verified", "Dry run: nothing recorded or spawned"
        elif authorized_pr_resume_covers(conn, task_id, snapshot,
                                        authorization_comment_id=authorization_comment_id):
            result.status = "already_recorded"
        else:
            kb._append_event(conn, task_id, AUTHORIZED_PR_RESUME_EVENT, {
                "actor": actor, "reason": reason.strip(), "verified_at": int(time.time()),
                "authorization": auth, "scope": scope, "comments": snapshot, "prs": result.prs,
            })
            result.status = "recorded"
    return result
