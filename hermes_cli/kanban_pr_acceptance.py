"""Exact-head GitHub acceptance for explicitly declared PR tasks.

Network work happens outside SQLite transactions. The lifecycle owner persists
receipts only after rechecking the captured run/status/contract under its lock.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
from urllib.parse import quote

import yaml

_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_PR = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)")


def validate_contract(value: str | None) -> str:
    if value is None or value == "local-only":
        return "local-only"
    if not isinstance(value, str) or not (_REPO.fullmatch(value) or _PR.fullmatch(value)):
        raise ValueError("completion_contract must be local-only, OWNER/REPO, or an exact GitHub PR URL")
    return value


def _api(endpoint: str, *, query: str | None = None, paginate: bool = False):
    command = ["gh", "api", endpoint, "--hostname", "github.com"]
    if query is not None:
        command += ["-f", "query=" + query]
    if paginate:
        command += ["--paginate", "--slurp"]
    result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=30, check=True)
    value = json.loads(result.stdout)
    if isinstance(value, dict) and value.get("errors"):
        raise ValueError("GitHub returned incomplete GraphQL evidence")
    return value


def collect_acceptance(contract: str, published_pr: str | None) -> dict:
    receipt = {"ok": False, "classification": "missing", "head_sha": None,
               "pr_url": published_pr, "checks": [],
               "recovery": "Fix required failures, rerun infrastructure checks or wait, then retry completion. "
                           "Use kanban_block if human input is needed; receipts remain on the task event log."}
    try:
        declared = _PR.fullmatch(contract)
        url = contract if declared else published_pr
        match = _PR.fullmatch(url or "")
        if not match or (not declared and match[1] != contract) or (declared and published_pr and published_pr != contract):
            receipt["detail"] = "Supply metadata.published_pr matching the persisted completion contract."
            return receipt
        repo, number = match[1], int(match[2])
        receipt["pr_url"] = url
        owner, name = repo.split("/")
        query = '''{repository(owner:%s,name:%s){pullRequest(number:%d){headRefOid headRefName baseRefName state
            baseRef{branchProtectionRule{requiredStatusChecks{context app{databaseId}}}}}}}''' % (
                json.dumps(owner), json.dumps(name), number)
        pr = _api("graphql", query=query)["data"]["repository"]["pullRequest"]
        sha, branch = pr["headRefOid"], pr["baseRefName"]
        receipt["head_sha"] = sha
        if not re.fullmatch(r"[0-9a-f]{40}", sha) or pr["state"] not in {"OPEN", "MERGED"}:
            raise ValueError("PR is closed or current head is unavailable")
        protection = (pr.get("baseRef") or {}).get("branchProtectionRule") or {}
        required = {(r["context"], (r.get("app") or {}).get("databaseId")) for r in protection.get("requiredStatusChecks", [])}
        rules = _api(f"repos/{repo}/rules/branches/{quote(branch, safe='')}?per_page=100", paginate=True)
        for page in rules:
            for rule in page:
                if rule["type"] == "required_status_checks":
                    required.update((r["context"], r.get("integration_id"))
                                    for r in rule["parameters"]["required_status_checks"])
        receipt["required"] = [{"context": c, "app_id": a} for c, a in sorted(required, key=str)]
        pages = _api(f"repos/{repo}/commits/{sha}/check-runs?per_page=100&filter=latest", paginate=True)
        runs = [run for page in pages for run in page["check_runs"]]
        if len({r["id"] for r in runs}) != pages[0]["total_count"]:
            raise ValueError("Incomplete check-run pagination")
        statuses = [{**s, "sha": sha} for page in _api(f"repos/{repo}/commits/{sha}/statuses?per_page=100", paginate=True) for s in page]
        # LOCAL-PATCH kanban-unprotected-ci: absence of branch protection does
        # not erase actual CI evidence or turn a published task into local-only.
        selected_checks = []
        if not required:
            latest_statuses = {s["context"]: s for s in sorted(statuses, key=lambda s: s["id"])}
            suite_pages = _api(f"repos/{repo}/commits/{sha}/check-suites?per_page=100", paginate=True)
            suites = [suite for page in suite_pages for suite in page["check_suites"]]
            if len({s["id"] for s in suites}) != suite_pages[0]["total_count"]:
                raise ValueError("Incomplete check-suite pagination")
            # GitHub auto-creates app suites on push even when an integration
            # never accepts work. An untouched optional placeholder is not CI.
            unreported = {s["id"] for s in suites if _unreported_app_suite(s)}
            receipt["unreported_suites"] = [{"id": s["id"], "app": s["app"]["name"],
                "head_sha": s["head_sha"], "status": s["status"]}
                for s in suites if s["id"] in unreported]
            selected_checks = runs + list(latest_statuses.values()) + [
                {**suite, "name": f"{suite['app']['name']} suite"}
                for suite in suites if suite["id"] not in unreported]
            receipt["policy"] = "all-reported-checks"
            if not selected_checks:
                workflows = _api(f"repos/{repo}/actions/workflows?per_page=100", paginate=True)
                entries = [w for page in workflows for w in page["workflows"]]
                if len({w["id"] for w in entries}) != workflows[0]["total_count"]:
                    raise ValueError("Incomplete workflow pagination")
                if any(_expects_head_checks(repo, sha, w, pr) for w in entries if w["state"] == "active"):
                    receipt.update(classification="pending", detail="CI workflows exist but this head has no check results yet.")
                    return receipt
                receipt.update(policy="no-checks-configured", detail="No required or reported checks; no active workflow expects checks for this head.")
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
            selected_checks.extend(selected)
        for check in selected_checks:
            is_run = "conclusion" in check
            outcome = check.get("conclusion") if is_run else check["state"]
            classification = _classify(check, sha, outcome, is_run)
            # Without required contexts every reported check counts, so a check
            # that deliberately did not run is not evidence of failure.
            if (not required and check.get("status") == "completed"
                    and outcome in {"neutral", "skipped"} and check.get("head_sha") == sha):
                classification = "success"
            outcomes.append(classification)
            receipt["checks"].append({"name": check.get("name", check.get("context")), "id": check["id"],
                "url": check.get("html_url") or check.get("target_url"),
                "head_sha": check.get("head_sha", check.get("sha")),
                "classification": classification, "conclusion": outcome})
        # Re-read after all pages: old-head successes are never transferable.
        current = _api(f"repos/{repo}/pulls/{number}")
        if current["head"]["sha"] != sha or current["base"]["ref"] != branch or (current["state"] == "closed" and not current.get("merged")):
            receipt.update(classification="stale", detail="PR head/base changed while collecting evidence; retry.")
            return receipt
        receipt["classification"] = next((x for x in outcomes if x != "success"),
            "success" if outcomes or receipt.get("policy") == "no-checks-configured" else "missing")
        receipt["ok"] = receipt["classification"] == "success"
        return receipt
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, IndexError):
        # Never persist gh stderr (credentials/host details); the failed phase is actionable.
        receipt.update(classification="infra", detail="GitHub acceptance evidence unavailable or incomplete; check gh authentication/API access and retry.")
        return receipt


def _unreported_app_suite(suite: dict) -> bool:
    return (suite.get("app", {}).get("slug") not in {None, "github-actions"}
            and suite.get("status") == "queued" and suite.get("conclusion") is None
            and suite.get("latest_check_runs_count") == 0
            and bool(suite.get("created_at")) and suite.get("created_at") == suite.get("updated_at"))


def _expects_head_checks(repo: str, sha: str, workflow: dict, pr: dict) -> bool:
    """Only branch push/PR triggers promise head CI; unrelated automation does not."""
    path = workflow["path"]
    if not path.startswith(".github/workflows/") or ".." in path.split("/"):
        raise ValueError("Workflow definition is unavailable")
    content = _api(f"repos/{repo}/contents/{quote(path, safe='/')}?ref={sha}")
    if content.get("encoding") != "base64":
        raise ValueError("Workflow definition is incomplete")
    try:
        definition = yaml.load(base64.b64decode(''.join(content['content'].split()), validate=True), Loader=yaml.BaseLoader)
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError("Workflow definition is invalid") from exc
    events = definition.get("on") if isinstance(definition, dict) else None
    if isinstance(events, str):
        events = [events]
    if not isinstance(events, (list, dict)) or not events or any(not isinstance(e, str) for e in events):
        raise ValueError("Workflow triggers are unavailable")
    if isinstance(events, list):
        events = dict.fromkeys(events)
    for event, branch in (("pull_request", pr["baseRefName"]), ("push", pr["headRefName"])):
        if event not in events:
            continue
        filters = events[event] or {}
        if not isinstance(filters, dict):
            raise ValueError("Workflow event filters are unavailable")
        if event == "push" and set(filters).intersection({"tags", "tags-ignore"}) and not set(filters).intersection({"branches", "branches-ignore"}):
            continue
        if event == "pull_request" and "types" in filters:
            if not set(filters["types"]).intersection({"opened", "reopened", "synchronize", "ready_for_review"}):
                continue
        if "branches" in filters and not _matches_ref(branch, filters["branches"]):
            continue
        if "branches-ignore" in filters and _matches_ref(branch, filters["branches-ignore"]):
            continue
        # Path/job expressions can still suppress a run. In the absence of
        # reported evidence, do not invent success for an applicable trigger.
        return True
    return False


def _matches_ref(branch: str, patterns: list) -> bool:
    """GitHub branch-filter globs, including ordered exclusion/re-inclusion."""
    if not isinstance(patterns, list) or not patterns or any(not isinstance(p, str) or not p for p in patterns):
        raise ValueError("Workflow branch filters are unavailable")
    matched = False
    for pattern in patterns:
        negative = pattern.startswith("!")
        pattern = pattern[1:] if negative else pattern
        parts = []
        for token in re.findall(r"\\.|\*\*/|\*\*|\*|\[[A-Za-z0-9-]+\]|.", pattern):
            if token.startswith("\\"):
                parts.append(re.escape(token[1:]))
            elif token in {"*", "**", "**/"}:
                parts.append({"*": "[^/]*", "**": ".*", "**/": "(?:.*/)?"}[token])
            elif token in {"?", "+"} or (token.startswith("[") and token.endswith("]")):
                parts.append(token)
            elif token in {"[", "]"}:
                raise ValueError("Invalid workflow branch pattern")
            else:
                parts.append(re.escape(token))
        try:
            if re.fullmatch(''.join(parts), branch):
                matched = not negative
        except re.error as exc:
            raise ValueError("Invalid workflow branch pattern") from exc
    return matched


def _classify(check: dict, sha: str, outcome: str | None, is_run: bool) -> str:
    if check.get("head_sha", check.get("sha")) != sha:
        return "stale"
    if is_run and check.get("status") != "completed":
        return "pending"
    return {"success": "success", "failure": "failure", "error": "infra", "pending": "pending"}.get(outcome, "infra")
