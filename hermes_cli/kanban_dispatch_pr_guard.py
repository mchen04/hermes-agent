from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hermes_cli.kanban_db_dispatch import (Optional, sqlite3)


def respawn_guard_verdict(
    conn: sqlite3.Connection, task_id: str, *, lane: str = "ready",
) -> tuple[Optional[str], dict]:
    """``(reason, detail)``: the guard reason (or None) and, for the PR holds,
    the URLs that hold (``open_prs`` / ``unknown_prs``).

    Called per ready/review row before any claim attempt. Priority order:
    ``"infrastructure_cooldown"`` (latest run is a ``spawn_failed`` the host
    refused — no restart-safe scope — within the cooldown; never counted),
    ``"rate_limit_cooldown"`` (latest run ``rate_limited`` within the cooldown;
    checked BEFORE ``blocker_auth`` because the requeue stamps a quota-flavored
    ``last_failure_error`` that would otherwise park the task forever — that
    path never increments ``consecutive_failures``), ``"blocker_auth"``
    (quota/auth pattern; the breaker still trips eventually), then for the
    ready lane only ``"recent_success"`` (completed run within the window, unless
    a re-queue event arrived after it — a deliberate re-run) and ``"active_pr"``
    (an OPEN GitHub PR linked from a recent comment; re-spawning risks a
    duplicate PR; ``"pr_state_unknown"`` when GitHub could not answer — unless a
    handoff event followed the comment: the named profile must work on that
    PR — or a separately recorded explicit
    ``authorized_pr_resume`` receipt binds an operator authorization to the
    current owner, contract, and PR comments). The review lane skips the last two:
    they are the *inputs* to a review handoff. Stale / dead claim locks are NOT a guard reason — the reclaim
    passes own those.
    """
    from hermes_cli.kanban_db_dispatch import (_guard_pr_state, _guarded_pr_comments, _manual_reclaim_after_own_pr)

    from hermes_cli.kanban_db_dispatch import (_RESPAWN_BLOCKER_RE, _RESPAWN_GUARD_SUCCESS_WINDOW, _is_handoff_event, _kb, _rows_since, time)

    row = conn.execute(
        "SELECT last_failure_error FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        return None, {}

    now = int(time.time())

    # 1. Rate-limit cooldown — see docstring for why this precedes blocker_auth.
    #    LATEST run only: a newer crash/completion supersedes the rate-limit run.
    #    An infrastructure spawn refusal (#114720) shares the cooldown: the host
    #    condition is not the card's, so it retries forever, spaced, and never
    #    reaches the breaker.
    rl_cooldown = _kb._resolve_rate_limit_cooldown_seconds()
    latest_run = conn.execute(
        "SELECT outcome, ended_at, metadata FROM task_runs "
        "WHERE task_id = ? AND ended_at IS NOT NULL "
        "ORDER BY ended_at DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    if latest_run is not None and latest_run["outcome"] == "spawn_failed":
        if rl_cooldown > 0 and _kb._json_dict(latest_run["metadata"]).get("infrastructure"):
            ended_at = _kb._to_epoch(latest_run["ended_at"])
            if ended_at is not None and (now - ended_at) < rl_cooldown:
                return "infrastructure_cooldown", {}
    if latest_run is not None and latest_run["outcome"] == "rate_limited":
        if rl_cooldown <= 0:
            # Cooldown disabled — respawn immediately, skipping blocker_auth so
            # the stamped rate-limit text doesn't re-trap the task.
            return None, {}
        ended_at = _kb._to_epoch(latest_run["ended_at"])
        if ended_at is not None and (now - ended_at) < rl_cooldown:
            return "rate_limit_cooldown", {}
        # Cooldown elapsed — return early so blocker_auth doesn't catch the
        # stamped rate-limit text; this path intentionally retries forever
        # (spaced by the cooldown) until quota returns or a real run supersedes it.
        return None, {}

    # 2. Quota / auth blocker: retrying immediately will not help.  A plain
    # crash is different: its persisted error includes the worker's last
    # captured output, which is context rather than a diagnosis and may contain
    # benign commands such as ``claude auth status`` (#117097).
    err = _kb._lossy_text(row["last_failure_error"])
    latest_outcome = latest_run["outcome"] if latest_run is not None else None
    if err and latest_outcome != "crashed" and _RESPAWN_BLOCKER_RE.search(err):
        return "blocker_auth", {}

    # Review-lane spawns stop here: a recent completed run and a fresh PR URL
    # are the canonical *inputs* to a review handoff, not duplicate-work signals.
    if lane == "review":
        return None, {}

    # 3. Completed run within guard window. Exception: an explicit re-queue
    #    AFTER that success (done→ready drag, re-promotion, unblock, reclaim) is
    #    a deliberate "run it again" — otherwise a manual done→ready would sit
    #    silently held until the window elapses.
    success = max((t for t, _ in _rows_since(
        conn, "task_runs", task_id, now - _RESPAWN_GUARD_SUCCESS_WINDOW, "AND outcome = 'completed'")), default=None)
    if success is not None and not any(_rows_since(
            conn, "task_events", task_id, success, "AND kind IN ('status', 'promoted', 'unblocked', 'reclaimed')")):
        return "recent_success", {}

    # 4. GitHub PR URL in a recent comment — prior worker already opened a PR.
    #    Exception: a handoff AFTER the newest PR comment (operator reassign,
    #    reviewer changes_requested, review reopen) names the profile that must
    #    now work on THAT PR — a closer or the implementer finishing it, not a
    #    duplicate implementation (#111910). A crash/reclaim is not a handoff,
    #    so the worker that opened the PR is still not re-spawned against it.
    pr_comments = _guarded_pr_comments(conn, task_id, now)
    if pr_comments:
        # Strictly after the newest PR comment: a same-second tie stays guarded (fail closed).
        if any(_is_handoff_event(e["kind"], e["payload"]) for _, e in _rows_since(
                conn, "task_events", task_id, pr_comments[0]["created_at"],
                "AND kind IN ('assigned', 'changes_requested', 'review_reopened')", cols=", kind, payload", strict=True)):
            return None, {}
        # LOCAL-PATCH kanban-authorized-pr-resume: an explicit operator receipt,
        # never permission inferred from ordinary progress prose.
        from hermes_cli.kanban_pr_resume import authorized_pr_resume_covers
        if authorized_pr_resume_covers(conn, task_id, pr_comments):
            return None, {}
        # LOCAL-PATCH kanban-pr-state-guard: merged/closed PRs never hold. A
        # failed lookup keeps the conservative hold under its own reason.
        states = {url: _guard_pr_state(url) for c in pr_comments for url in c["urls"]}
        holding = [c for c in pr_comments if any(states[u] in ("open", "unknown") for u in c["urls"])]
        if not holding:
            return None, {}
        # An operator reclaim after the card's own worker linked its PR is a
        # deliberate "restart this card": the fresh worker reads that PR on the
        # card, so it is no duplicate risk. A crash reclaim is not manual.
        if _manual_reclaim_after_own_pr(conn, task_id, holding):
            return None, {}
        open_prs = sorted(u for u, st in states.items() if st == "open")
        if open_prs:
            return "active_pr", {"open_prs": open_prs}
        return "pr_state_unknown", {"unknown_prs": sorted(u for u, st in states.items() if st == "unknown")}

    return None, {}


def _guarded_pr_comments(conn: sqlite3.Connection, task_id: str, now: int) -> list[dict]:
    """Comments inside the ``active_pr`` window that carry a GitHub PR URL,
    newest first: ``{id, created_at, sha256, urls}``. The body hash binds a
    PR-resume authorization to the exact text it was granted against."""
    from hermes_cli.kanban_db_dispatch import (_RESPAWN_GUARD_PR_URL_RE, _RESPAWN_GUARD_PR_WINDOW, _kb, _rows_since, hashlib)

    out = []
    # Text stamps are decided on their parsed epoch; an unreadable one counts as now (duplicate-PR risk: fail closed).
    for t, c in _rows_since(conn, "task_comments", task_id, now - _RESPAWN_GUARD_PR_WINDOW,
                            cols=", id, body", unreadable=now):
        body = _kb._lossy_text(c["body"])
        urls = sorted({m.group(0) for m in _RESPAWN_GUARD_PR_URL_RE.finditer(body or "")})
        if urls:
            out.append({"id": int(c["id"]), "created_at": t,
                        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(), "urls": urls})
    out.sort(key=lambda c: (c["created_at"], c["id"]), reverse=True)
    return out


def _github_guard_pr_state(url: str) -> str:
    """``open``/``merged``/``closed`` for one PR URL via the ambient ``gh`` login
    (the operator's). Raises on any failure."""
    from hermes_cli.kanban_db_dispatch import (_GUARD_PR_LOOKUP_TIMEOUT, _GUARD_PR_PARTS_RE)

    from hermes_cli import kanban_pr_acceptance as acc

    match = _GUARD_PR_PARTS_RE.fullmatch(url)
    if not match:
        raise ValueError("unrecognised PR URL")
    owner, repo, number = match[1], match[2], int(match[3])
    data = acc._api(f"repos/{owner}/{repo}/pulls/{number}", timeout=_GUARD_PR_LOOKUP_TIMEOUT)
    raw = data.get("state") if isinstance(data, dict) else None
    if raw == "open" and not data.get("merged_at"):
        return "open"
    if raw == "closed":
        return "merged" if data.get("merged_at") else "closed"
    raise ValueError(f"unexpected PR state {raw!r}")


def _guard_pr_state(url: str) -> str:
    """Cached :func:`_github_guard_pr_state`; ``unknown`` when the lookup fails."""
    from hermes_cli.kanban_db_dispatch import (_github_guard_pr_state)

    from hermes_cli.kanban_db_dispatch import (_GUARD_PR_STATE_TTL, _guard_pr_state_cache, time)

    now = time.monotonic()
    key = url.lower()
    hit = _guard_pr_state_cache.get(key)
    if hit is not None and now < hit[1]:
        return hit[0]
    try:
        state = _github_guard_pr_state(url)
    except Exception as exc:  # network, auth, timeout, malformed: never raise into a tick
        import logging
        logging.getLogger(__name__).info("kanban active_pr: PR state lookup failed for %s: %s",
                                         url, type(exc).__name__, exc_info=True)
        state = "unknown"
    if len(_guard_pr_state_cache) > 512:
        _guard_pr_state_cache.clear()
    _guard_pr_state_cache[key] = (state, now + _GUARD_PR_STATE_TTL.get(state, 60))
    return state


def _manual_reclaim_after_own_pr(conn: sqlite3.Connection, task_id: str, holding: list[dict]) -> bool:
    """True when every holding comment was written by this card's own worker and a
    manual (operator) reclaim came strictly after the newest of them."""
    from hermes_cli.kanban_db_dispatch import (_kb, _rows_since)

    own: set[int] = set()
    for e in conn.execute(
        "SELECT payload FROM task_events WHERE task_id = ? AND kind = 'commented'", (task_id,),
    ).fetchall():
        data = _kb._json_dict(e["payload"])
        if (data.get("origin") == "worker" and data.get("worker_task_id") == task_id
                and type(data.get("comment_id")) is int):
            own.add(data["comment_id"])
    if not all(c["id"] in own for c in holding):
        return False
    newest = max(c["created_at"] for c in holding)
    for _, e in _rows_since(conn, "task_events", task_id, newest, "AND kind = 'reclaimed'", cols=", payload", strict=True):
        if _kb._json_dict(e["payload"]).get("manual") is True:
            return True
    return False

