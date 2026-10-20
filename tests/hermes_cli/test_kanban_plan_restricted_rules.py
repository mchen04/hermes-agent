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
    state = {"pr_state": "MERGED", "runs": [], "statuses": [], "rules_error": "plan",
             "repo": "acme/repo", "number": 7, "pull": {}, "reads": []}

    def fake_api(endpoint, *, query=None, paginate=False, profile_home=None, repo=None):
        state["reads"].append(endpoint)
        if endpoint == "graphql":
            assert repo == state["repo"]  # GraphQL acceptance names its repository explicitly
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
            if isinstance(state["pull"], Exception):
                raise state["pull"]
            owner = state["repo"].split("/")[0]
            return {"number": state["number"], "head": {"sha": "a" * 40}, "state": "closed", "merged": True,
                    "base": {"ref": "main", "repo": {"full_name": state["repo"], "owner": {"login": owner}}},
                    **state["pull"]}
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


# LOCAL-PATCH kanban-advisory-checks: on mchen04's repositories head checks are advisory.
MINE = "https://github.com/mchen04/my-ai-skills/pull/164"


@pytest.fixture
def mine(github):
    github.update(repo="mchen04/my-ai-skills", number=164)
    return github


def _mine():
    return acceptance.collect_acceptance("mchen04/my-ai-skills", MINE)


def test_mchen04_merged_pr_with_a_failed_check_is_accepted_and_keeps_the_failure(mine):
    mine["runs"] = [_run("success"), {**_run("failure"), "id": 2, "name": "check"}]
    receipt = _mine()
    assert receipt["ok"] is True and receipt["classification"] == "advisory"
    assert receipt["advisory_owner"] == "mchen04" and receipt["required"] == []
    assert [(c["name"], c["classification"], c["conclusion"]) for c in receipt["checks"]] == [
        ("ci", "success", "success"), ("check", "failure", "failure")]
    assert "PR merged at head " + "a" * 40 in receipt["detail"] and "check failure" in receipt["detail"]


def test_mchen04_merged_pr_with_a_running_check_or_failed_status_is_accepted(mine):
    mine["runs"] = [_run(None, status="in_progress")]
    mine["statuses"] = [{"id": 5, "context": "local", "state": "failure", "sha": "a" * 40}]
    receipt = _mine()
    assert receipt["ok"] is True and receipt["classification"] == "advisory"
    assert [c["classification"] for c in receipt["checks"]] == ["pending", "failure"]


def test_mchen04_passing_checks_read_as_before(mine):
    mine["runs"] = [_run("success")]
    receipt = _mine()
    assert receipt["ok"] is True and receipt["classification"] == "success"
    assert not any("/pulls/" in r for r in mine["reads"])


def test_mchen04_open_pr_is_still_refused(mine):
    mine["pr_state"] = "OPEN"
    mine["runs"] = [_run("failure")]
    receipt = _mine()
    assert receipt["ok"] is False and "No repository-required checks" in receipt["detail"]


def test_mchen04_head_moved_during_collection_is_stale(mine):
    mine["runs"] = [_run("failure")]
    mine["pull"] = {"head": {"sha": "b" * 40}}
    receipt = _mine()
    assert receipt["ok"] is False and receipt["classification"] == "stale"


@pytest.mark.parametrize("pull", [
    {"base": {"ref": "main", "repo": {"full_name": "Epoch-ML/my-ai-skills", "owner": {"login": "Epoch-ML"}}}},
    {"base": {"ref": "main", "repo": {"full_name": "mchen04x/my-ai-skills", "owner": {"login": "mchen04x"}}}},
    {"base": {"ref": "main", "repo": {"full_name": "mchen04/other", "owner": {"login": "mchen04"}}}},
    {"base": {"ref": "main"}},
    {"number": 165},
    {"merged": False},
], ids=["transferred", "lookalike-owner", "other-repo", "no-repo", "other-number", "not-merged"])
def test_mchen04_claim_github_does_not_confirm_keeps_the_gate(mine, pull):
    mine["runs"] = [_run("failure")]
    mine["pull"] = pull
    receipt = _mine()
    assert receipt["ok"] is False and receipt["classification"] == "failure"
    assert "Checks stayed a gate" in receipt["detail"] and "advisory_owner" not in receipt


def test_mchen04_reread_refused_is_auth(mine):
    mine["runs"] = [_run("failure")]
    mine["pull"] = acceptance._GateAuthError("HTTP 404 on repos/mchen04/my-ai-skills/pulls/164")
    receipt = _mine()
    assert receipt["ok"] is False and receipt["classification"] == "auth"


@pytest.mark.parametrize("repo", ["Epoch-ML/zerg", "mchen04x/repo", "xmchen04/repo", "mchen/repo"])
def test_other_owners_keep_the_gate_with_unchanged_reads(github, repo):
    github.update(repo=repo, number=7)
    github["runs"] = [_run("failure")]
    receipt = acceptance.collect_acceptance(repo, f"https://github.com/{repo}/pull/7")
    assert receipt["ok"] is False and receipt["classification"] == "failure"
    assert receipt["detail"] == "PR merged, but a check on its head did not pass."
    assert not any("/pulls/" in r for r in github["reads"])
