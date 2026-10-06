"""LOCAL-PATCH kanban-plan-restricted-rules: GitHub's free plan hides branch rules on
private repos with HTTP 403 "Upgrade to GitHub Pro". That is not an auth failure: such a
repo enforces no required checks, so a merged PR is accepted once no head check failed.
Any other 403 is still auth."""
import subprocess

import pytest

from hermes_cli import kanban_pr_acceptance as acceptance

PR = "https://github.com/acme/repo/pull/7"
PLAN = ("gh: Upgrade to GitHub Pro or make this repository public to enable this feature. "
        "(HTTP 403)")


@pytest.fixture
def github(monkeypatch):
    state = {"pr_state": "MERGED", "runs": [], "statuses": [], "rules_error": "plan"}

    def fake_api(endpoint, *, query=None, paginate=False, profile_home=None):
        if endpoint == "graphql":
            return {"data": {"repository": {"pullRequest": {
                "headRefOid": "a" * 40, "baseRefName": "main", "state": state["pr_state"],
                "baseRef": {"branchProtectionRule": None}}}}}
        if "/rules/branches/" in endpoint:
            if state["rules_error"] == "plan":
                raise acceptance._PlanRestricted(endpoint)
            if state["rules_error"] == "auth":
                raise acceptance._GateAuthError("HTTP 403 on rules")
            return [[]]
        if "/check-runs" in endpoint:
            return [{"total_count": len(state["runs"]), "check_runs": state["runs"]}]
        if "/statuses" in endpoint:
            return [state["statuses"]]
        if "/pulls/" in endpoint:
            return {"head": {"sha": "a" * 40}, "base": {"ref": "main"}, "state": "closed", "merged": True}
        raise AssertionError(f"unexpected GitHub read {endpoint}")

    monkeypatch.setattr(acceptance, "_api", fake_api)
    return state


def _run(conclusion, status="completed", sha="a" * 40):
    return {"id": 1, "name": "ci", "head_sha": sha, "status": status,
            "conclusion": conclusion, "app": {"id": 1}}


def test_merged_pr_with_passing_checks_is_accepted(github):
    github["runs"] = [_run("success"), {**_run("skipped"), "id": 2}]
    receipt = acceptance.collect_acceptance("acme/repo", PR)
    assert receipt["ok"] is True and receipt["classification"] == "success"
    assert "plan" in receipt["detail"]


def test_merged_pr_with_a_failed_check_is_refused(github):
    github["runs"] = [_run("success"), {**_run("failure"), "id": 2}]
    receipt = acceptance.collect_acceptance("acme/repo", PR)
    assert receipt["ok"] is False and receipt["classification"] == "failure"


def test_merged_pr_with_a_running_check_waits(github):
    github["runs"] = [_run(None, status="in_progress")]
    receipt = acceptance.collect_acceptance("acme/repo", PR)
    assert receipt["ok"] is False and receipt["classification"] == "pending"


def test_merged_pr_with_a_failed_legacy_status_is_refused(github):
    github["statuses"] = [{"id": 5, "context": "ci", "state": "failure", "sha": "a" * 40}]
    receipt = acceptance.collect_acceptance("acme/repo", PR)
    assert receipt["ok"] is False and receipt["classification"] == "failure"


def test_open_pr_is_still_refused(github):
    github["pr_state"] = "OPEN"
    github["runs"] = [_run("success")]
    receipt = acceptance.collect_acceptance("acme/repo", PR)
    assert receipt["ok"] is False


def test_other_403_on_rules_is_still_auth(github):
    github["rules_error"] = "auth"
    receipt = acceptance.collect_acceptance("acme/repo", PR)
    assert receipt["ok"] is False and receipt["classification"] == "auth"


@pytest.mark.parametrize("stderr,expected", [
    (PLAN, "_PlanRestricted"),
    ("gh: Resource not accessible by integration (HTTP 403)", "_GateAuthError"),
    ("gh: Not Found (HTTP 404)", "_GateAuthError"),
])
def test_api_tells_plan_restriction_from_auth(monkeypatch, stderr, expected):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], output="", stderr=stderr)
    monkeypatch.setattr(acceptance.subprocess, "run", fail)
    with pytest.raises(getattr(acceptance, expected)):
        acceptance._api("repos/acme/repo/rules/branches/main")
