"""Exact-head GitHub acceptance for explicitly declared PR tasks.

Network work happens outside SQLite transactions. The lifecycle owner persists
receipts only after rechecking the captured run/status/contract under its lock.

``gh`` runs as the card's assignee profile (``profile_home``), not the ambient
login: :func:`_gh_env` resolves that profile's own credentials/config for the
subprocess — a multi-profile host's default ``gh`` login cannot read another
org's private repos (#122689).

``kanban.github_read_transport`` (opt-in, exact OWNER/REPO) instead runs a repository's reads
on another host's existing ``gh`` login over ssh — see :func:`_read_route`.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_PR = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)")


def validate_contract(value: str | None) -> str:
    if value is None or value == "local-only":
        return "local-only"
    if not isinstance(value, str) or not (_REPO.fullmatch(value) or _PR.fullmatch(value)):
        raise ValueError("completion_contract must be local-only, OWNER/REPO, or an exact GitHub PR URL")
    return value


def _api(endpoint: str, *, query: str | None = None, paginate: bool = False,
         profile_home: str | None = None, timeout: int = 30, repo: str | None = None):
    repo = _endpoint_repo(endpoint, query, repo)
    route = _read_route(repo, profile_home) if repo else None
    if route is None:
        command = ["gh", "api", endpoint, "--hostname", "github.com"]
        if query is not None:
            command += ["-f", "query=" + query]
        if paginate:
            command += ["--paginate", "--slurp"]
        env = _gh_env(profile_home)
    else:
        command = _remote_command(route, endpoint, query, paginate, repo)
        from tools.environments.local import hermes_subprocess_env
        env = hermes_subprocess_env()  # GitHub secrets scrubbed; the token never exists locally
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=timeout,
                                check=True, env=env)
    except subprocess.CalledProcessError as exc:
        # 401/403/404 = the login cannot see this repository (wrong profile identity
        # or missing grant), not a transient API failure. Persist only the status
        # code + endpoint, never gh's stderr (credentials/host details).
        # LOCAL-PATCH kanban-plan-restricted-rules: GitHub's free plan answers rules reads on a
        # private repo with this 403; the repo enforces no rules, so it is not an identity problem.
        if "Upgrade to GitHub Pro" in (exc.stderr or ""):
            raise _PlanRestricted(endpoint.split('?')[0]) from None
        denied = re.search(r"HTTP (40[134])", exc.stderr or "")
        if denied:
            raise _GateAuthError(f"HTTP {denied[1]} on {endpoint.split('?')[0]}") from None
        if exc.returncode == 4:  # gh's authentication-required exit: this profile has no login
            raise _GateAuthError(f"gh has no login for {endpoint.split('?')[0]}") from None
        raise
    value = json.loads(result.stdout)
    if isinstance(value, dict) and value.get("errors"):
        raise ValueError("GitHub returned incomplete GraphQL evidence")
    return value


# LOCAL-PATCH kanban-github-read-transport: an opt-in, exact-repository route that runs the
# read on another host's existing ``gh`` login over ssh, as one named account selected per
# command (``gh auth token --user``). Only the endpoints the acceptance/guard reads use pass.
_REST_REPO = re.compile(r"repos/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/(.*)")
_ROUTED_REST = re.compile(
    r"(?:pulls/[1-9][0-9]*|commits/[0-9a-f]{40}/(?:check-runs|statuses)|rules/branches/[A-Za-z0-9%._~-]+)"
    r"(?:\?[A-Za-z0-9_.=&-]*)?")
_GRAPHQL_REPO = re.compile(r'\s*\{repository\(owner:("[A-Za-z0-9_.-]+"),name:("[A-Za-z0-9_.-]+")\)\{')
# The one GraphQL document a route may carry: collect_acceptance's query, byte for byte.
_ACCEPTANCE_QUERY = '''{repository(owner:%s,name:%s){pullRequest(number:%d){headRefOid baseRefName state
            baseRef{branchProtectionRule{requiredStatusChecks{context app{databaseId}}}}}}}'''
_ACCEPTANCE_QUERY_SHAPE = re.compile(re.escape(_ACCEPTANCE_QUERY).replace("%s", '"([A-Za-z0-9_.-]+)"', 1)
                                     .replace("%s", '"([A-Za-z0-9_.-]+)"', 1).replace("%d", "([1-9][0-9]{0,9})"))
_SSH_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}")
_GH_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")
_GH_EXE = re.compile(r"(?:/[A-Za-z0-9_.+-]+)*/gh")
_ROUTE_KEYS = frozenset({"ssh_host", "gh_user", "gh"})


def _acceptance_query(repo: str, number: int) -> str:
    owner, name = repo.split("/")
    return _ACCEPTANCE_QUERY % (json.dumps(owner), json.dumps(name), number)


def _is_acceptance_query(query: str | None, repo: str) -> bool:
    """True only for collect_acceptance's exact query of ``repo``: one repository, one positive
    32-bit PR number, the fixed field tree — no alias, extra root/field, or other operation."""
    match = _ACCEPTANCE_QUERY_SHAPE.fullmatch(query or "")
    if not match or f"{match[1]}/{match[2]}".lower() != repo.lower() or int(match[3]) > 2**31 - 1:
        return False
    return query == _acceptance_query(f"{match[1]}/{match[2]}", int(match[3]))


def _endpoint_repo(endpoint: str, query: str | None, repo: str | None) -> str | None:
    """OWNER/REPO an ``_api`` call reads, or None (never routed). GraphQL is routable only when
    the caller declares ``repo=`` (historical callers without it keep the local login); a declared
    repo must agree with the query's repository or the REST path."""
    if repo is not None and not _REPO.fullmatch(repo):
        raise ValueError("Malformed repository for GitHub read")
    if endpoint == "graphql":
        if repo is None:
            return None
        match = _GRAPHQL_REPO.match(query or "")
        if not match or f"{json.loads(match[1])}/{json.loads(match[2])}".lower() != repo.lower():
            raise ValueError("GraphQL query does not match its declared repository")
        return repo
    match = _REST_REPO.fullmatch(endpoint)
    path_repo = f"{match[1]}/{match[2]}" if match else None
    if repo is not None and (path_repo or "").lower() != repo.lower():
        raise ValueError("GitHub endpoint does not match its declared repository")
    return path_repo


def _read_route(repo: str, profile_home: str | None) -> dict | None:
    """The configured route for exactly ``repo``, or None. Read per call (edits apply without a
    reload) from ONE file: the assignee profile's config.yaml when the read acts for a profile,
    else the root config.yaml (operator/dispatcher reads). A profile never inherits the root's
    route. A missing file, an absent key or no matching entry keeps the local login. A config
    that cannot be read or parsed, a table that is not a mapping, or a matching but malformed
    route cannot prove the route absent, so the read fails closed."""
    from hermes_constants import get_default_hermes_root
    from utils import fast_safe_load

    path = Path(profile_home or get_default_hermes_root()) / "config.yaml"
    try:
        with open(path, encoding="utf-8-sig") as f:
            config = fast_safe_load(f) or {}
    except FileNotFoundError:
        return None
    except Exception:
        raise _GateAuthError("config.yaml unreadable; GitHub read route unknown") from None
    kanban = config.get("kanban") if isinstance(config, dict) else None
    table = kanban.get("github_read_transport") if isinstance(kanban, dict) else None
    if table is None:
        return None
    if not isinstance(table, dict):
        raise _GateAuthError("kanban.github_read_transport must be a mapping")
    matches = [value for key, value in table.items()
               if isinstance(key, str) and key.lower() == repo.lower()]
    if not matches:
        return None
    route = matches[0]
    if (len(matches) != 1 or not _REPO.fullmatch(repo) or not isinstance(route, dict)
            or set(route) != _ROUTE_KEYS or not all(isinstance(v, str) for v in route.values())
            or not _SSH_HOST.fullmatch(route["ssh_host"]) or not _GH_LOGIN.fullmatch(route["gh_user"])
            or not _GH_EXE.fullmatch(route["gh"]) or "/../" in route["gh"] + "/" or "/./" in route["gh"] + "/"):
        raise _GateAuthError(f"kanban.github_read_transport route for {repo} is malformed")
    return route


def _remote_command(route: dict, endpoint: str, query: str | None, paginate: bool,
                    repo: str) -> list[str]:
    """ssh argv for one read. The remote script fetches the named account's token into a shell
    variable (never argv), refuses an empty token (gh would otherwise fall back to its active
    login), and execs ``gh api`` with fully quoted arguments. Exit 4 = no such login (auth)."""
    from shlex import quote

    gh = quote(route["gh"])
    if endpoint == "graphql":
        if paginate or not _is_acceptance_query(query, repo):
            raise ValueError("Only the exact acceptance GraphQL query is an allowed routed read")
        args = [route["gh"], "api", "graphql", "--hostname", "github.com", "-f", "query=" + query]
    else:
        match = _REST_REPO.fullmatch(endpoint)
        if query is not None or not match or not _ROUTED_REST.fullmatch(match[3]):
            raise ValueError("GitHub endpoint is not an allowed routed read")
        args = [route["gh"], "api", "--method", "GET", endpoint, "--hostname", "github.com"]
        if paginate:
            args += ["--paginate", "--slurp"]
    # The token travels only as a shell variable / exported env, never as an argv word.
    others = "-u GITHUB_TOKEN -u GH_ENTERPRISE_TOKEN -u GITHUB_ENTERPRISE_TOKEN"
    script = (f't="$(env -u GH_TOKEN {others} GH_PROMPT_DISABLED=1 {gh} auth token --hostname github.com '
              f'--user {quote(route["gh_user"])} 2>/dev/null)" && [ -n "$t" ] || exit 4; '
              f'GH_TOKEN="$t" exec env {others} ' + " ".join(quote(a) for a in args))
    return ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ForwardAgent=no",
            "-o", "ClearAllForwardings=yes", "--", route["ssh_host"], "/bin/sh -c " + quote(script)]


class _PlanRestricted(RuntimeError):
    """LOCAL-PATCH kanban-plan-restricted-rules: GitHub refused a rules read because the
    owner's plan has no branch rules on private repos (HTTP 403 "Upgrade to GitHub Pro")."""


class _GateAuthError(RuntimeError):
    """gh was refused at HTTP 401/403/404 (or GraphQL returned no repository):
    this profile's login cannot see the repo — an identity problem to fix, not
    an infrastructure blip to retry."""


def _gh_env(profile_home: str | None) -> dict[str, str] | None:
    """Child env for ``gh``: the card's profile identity when one is resolvable.

    The completion boundary runs in the worker (assignee), the CLI, or a
    reviewer/dispatcher turn, so an ambient ``gh`` login is whichever process
    happened to call it (#122689). ``served_profile_child_env(inherit_credentials=True)``
    is the seam for "this child acts for that profile": it scrubs the launch
    profile's credential residue and overlays the target profile's own
    ``GH_TOKEN``/``GH_CONFIG_DIR`` (its ``.env`` + external secret sources).
    ``None`` keeps the ambient env — unassigned cards behave exactly as before.
    """
    if not profile_home:
        return None
    from tools.environments.local import _is_routed_home, hermes_subprocess_env, served_profile_child_env
    base = hermes_subprocess_env(inherit_credentials=True)
    routed = _is_routed_home(profile_home)
    if routed:
        # gh's config dir decides which login `gh api` uses, yet it is a path, not a
        # credential, so no scrub list sees it; the target's own value is overlaid from its .env.
        base.pop("GH_CONFIG_DIR", None)
    env = served_profile_child_env(base=base, target_home=profile_home, inherit_credentials=True)
    if routed and not (env.keys() & {"GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR"}):
        # HOME/XDG_CONFIG_HOME are still the launch process's: without a login of its own the
        # child would fall through to ~/.config/gh/hosts.yml — the ambient login. Pin gh's config
        # to a profile-owned dir so it fails "not logged in" (exit 4 -> auth) instead.
        env["GH_CONFIG_DIR"] = str(Path(profile_home) / "gh")
    return env


def _assignee_profile_home(assignee: str | None) -> str | None:
    """Home whose ``gh`` login must read the contract repo — the assignee's, resolved
    exactly as the dispatcher resolves the worker's home — or None (unassigned) so the
    ambient login is used. An assigned card whose profile cannot be resolved is an
    identity failure (``auth``), never a silent fall-through to the ambient login."""
    if not assignee:
        return None
    from hermes_cli.profiles import normalize_profile_name, resolve_profile_env
    try:
        return resolve_profile_env(normalize_profile_name(assignee))
    except (FileNotFoundError, ValueError):
        raise _GateAuthError(f"assignee profile {assignee!r} cannot be resolved") from None


def collect_acceptance(contract: str, published_pr: str | None,
                       assignee: str | None = None) -> dict:
    receipt = {"ok": False, "classification": "missing", "head_sha": None,
               "pr_url": published_pr, "checks": [],
               "recovery": "Fix required failures, rerun infrastructure checks or wait, then retry completion. "
                           "Use kanban_block if human input is needed; receipts remain on the task event log."}
    try:
        profile_home = _assignee_profile_home(assignee)
        declared = _PR.fullmatch(contract)
        url = contract if declared else published_pr
        match = _PR.fullmatch(url or "")
        if not match or (not declared and match[1] != contract) or (declared and published_pr and published_pr != contract):
            receipt["detail"] = "Supply metadata.published_pr matching the persisted completion contract."
            return receipt
        repo, number = match[1], int(match[2])
        receipt["pr_url"] = url
        repository = _api("graphql", query=_acceptance_query(repo, number), profile_home=profile_home,
                          repo=repo)["data"]["repository"]
        if repository is None:
            # A private repo the login cannot read resolves to null, not an error.
            raise _GateAuthError(f"HTTP 404 on graphql {repo}")
        pr = repository["pullRequest"]
        sha, branch = pr["headRefOid"], pr["baseRefName"]
        receipt["head_sha"] = sha
        if not re.fullmatch(r"[0-9a-f]{40}", sha) or pr["state"] not in {"OPEN", "MERGED"}:
            raise ValueError("PR is closed or current head is unavailable")
        protection = (pr.get("baseRef") or {}).get("branchProtectionRule") or {}
        required = {(r["context"], (r.get("app") or {}).get("databaseId")) for r in protection.get("requiredStatusChecks", [])}
        try:
            rules = _api(f"repos/{repo}/rules/branches/{quote(branch, safe='')}?per_page=100",
                         paginate=True, profile_home=profile_home)
        except _PlanRestricted:
            if pr["state"] == "MERGED" and not required:
                return _accept_plan_restricted_merge(receipt, repo, sha, profile_home)
            rules = []
        for page in rules:
            for rule in page:
                if rule["type"] == "required_status_checks":
                    required.update((r["context"], r.get("integration_id"))
                                    for r in rule["parameters"]["required_status_checks"])
        receipt["required"] = [{"context": c, "app_id": a} for c, a in sorted(required, key=str)]
        if not required:
            # LOCAL-PATCH kanban-merged-no-required-checks: a merge needs the operator's
            # authorization, so a merged PR is the acceptance when the repository
            # requires no checks. An open PR still has no evidence to accept.
            if pr["state"] == "MERGED":
                receipt.update(ok=True, classification="success",
                               detail="PR merged; the repository requires no checks.")
                return receipt
            receipt["detail"] = "No repository-required checks are configured; merge the PR or explicitly use a local-only contract for non-CI tasks."
            return receipt
        pages = _api(f"repos/{repo}/commits/{sha}/check-runs?per_page=100&filter=latest",
                     paginate=True, profile_home=profile_home)
        runs = [run for page in pages for run in page["check_runs"]]
        if len({r["id"] for r in runs}) != pages[0]["total_count"]:
            raise ValueError("Incomplete check-run pagination")
        statuses = [{**s, "sha": sha} for page in _api(f"repos/{repo}/commits/{sha}/statuses?per_page=100",
                                                       paginate=True, profile_home=profile_home) for s in page]
        outcomes = []
        for context, app_id in sorted(required, key=str):
            matching = [r for r in runs if r["name"] == context and
                        (app_id in (None, -1) or r["app"]["id"] == app_id)]
            # A legacy status can satisfy an unpinned context, but never a check pinned to an app.
            legacy = [s for s in statuses if s["context"] == context] if app_id in (None, -1) else []
            selected = matching + ([max(legacy, key=lambda s: s["id"])] if legacy else [])
            if not selected:
                outcomes.append("missing")
                receipt["checks"].append({"name": context, "classification": "missing", "head_sha": sha})
            for check in selected:
                is_run = "conclusion" in check
                outcome = check.get("conclusion") if is_run else check["state"]
                classification = _classify(check, sha, outcome, is_run)
                outcomes.append(classification)
                receipt["checks"].append({"name": context, "id": check["id"],
                    "url": check.get("html_url") or check.get("target_url"),
                    "head_sha": check.get("head_sha", check.get("sha")),
                    "classification": classification, "conclusion": outcome})
        # Re-read after all pages: old-head successes are never transferable.
        current = _api(f"repos/{repo}/pulls/{number}", profile_home=profile_home)
        if current["head"]["sha"] != sha or current["base"]["ref"] != branch or (current["state"] == "closed" and not current.get("merged")):
            receipt.update(classification="stale", detail="PR head/base changed while collecting evidence; retry.")
            return receipt
        receipt["classification"] = next((x for x in outcomes if x != "success"), "missing" if not outcomes else "success")
        receipt["ok"] = receipt["classification"] == "success"
        return receipt
    except _GateAuthError as exc:
        login = f"assignee profile {assignee!r}'s gh login" if assignee else "the ambient gh login"
        receipt.update(classification="auth",
                       detail=f"GitHub refused the acceptance read ({exc}) as {login}; "
                              "fix that profile's GitHub credentials/access to the repository, then retry completion.")
        return receipt
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, IndexError):
        # Never persist gh stderr (credentials/host details); the failed phase is actionable.
        receipt.update(classification="infra", detail="GitHub acceptance evidence unavailable or incomplete; check gh authentication/API access and retry.")
        return receipt


def _accept_plan_restricted_merge(receipt: dict, repo: str, sha: str, profile_home: str | None) -> dict:
    """LOCAL-PATCH kanban-plan-restricted-rules: a merged PR on a plan without branch rules
    is accepted when no check on its head failed or is still running."""
    pages = _api(f"repos/{repo}/commits/{sha}/check-runs?per_page=100&filter=latest",
                 paginate=True, profile_home=profile_home)
    runs = [run for page in pages for run in page["check_runs"]]
    statuses = [s for page in _api(f"repos/{repo}/commits/{sha}/statuses?per_page=100",
                                   paginate=True, profile_home=profile_home) for s in page]
    latest = {}
    for status in statuses:
        if status["context"] not in latest or status["id"] > latest[status["context"]]["id"]:
            latest[status["context"]] = {**status, "sha": sha}
    outcomes = []
    for check in [*runs, *latest.values()]:
        is_run = "conclusion" in check
        outcome = check.get("conclusion") if is_run else check["state"]
        if is_run and outcome in {"neutral", "skipped"}:
            outcome = "success"
        classification = _classify(check, sha, outcome, is_run)
        outcomes.append(classification)
        receipt["checks"].append({"name": check.get("name", check.get("context")), "id": check["id"],
            "head_sha": check.get("head_sha", check.get("sha")),
            "classification": classification, "conclusion": outcome})
    receipt["required"] = []
    bad = next((x for x in outcomes if x != "success"), None)
    if bad:
        receipt.update(classification=bad, detail="PR merged, but a check on its head did not pass.")
        return receipt
    receipt.update(ok=True, classification="success",
                   detail=f"PR merged; GitHub's plan has no branch rules on this private repo; "
                          f"{len(outcomes)} head checks passed.")
    return receipt


def _classify(check: dict, sha: str, outcome: str | None, is_run: bool) -> str:
    if check.get("head_sha", check.get("sha")) != sha:
        return "stale"
    if is_run and check.get("status") != "completed":
        return "pending"
    return {"success": "success", "failure": "failure", "error": "infra", "pending": "pending"}.get(outcome, "infra")
