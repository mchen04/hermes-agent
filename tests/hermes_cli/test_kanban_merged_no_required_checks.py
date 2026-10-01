"""LOCAL-PATCH kanban-merged-no-required-checks: a merged PR is the acceptance
when the repository requires no checks; an open PR there is still refused."""
import pytest

from hermes_cli import kanban_pr_acceptance as acceptance

PR = "https://github.com/acme/repo/pull/7"


@pytest.fixture
def github(monkeypatch):
    state = {"pr_state": "MERGED", "required": []}

    def fake_api(endpoint, *, query=None, paginate=False, profile_home=None):
        if endpoint == "graphql":
            rule = {"requiredStatusChecks": state["required"]} if state["required"] else None
            return {"data": {"repository": {"pullRequest": {
                "headRefOid": "a" * 40, "baseRefName": "main", "state": state["pr_state"],
                "baseRef": {"branchProtectionRule": rule}}}}}
        if "/rules/branches/" in endpoint or "/statuses" in endpoint:
            return [[]]
        if "/check-runs" in endpoint:
            return [{"total_count": 0, "check_runs": []}]
        if "/pulls/" in endpoint:
            return {"head": {"sha": "a" * 40}, "base": {"ref": "main"}, "state": "closed", "merged": True}
        raise AssertionError(f"unexpected GitHub read {endpoint}")

    monkeypatch.setattr(acceptance, "_api", fake_api)
    return state


def test_merged_pr_without_required_checks_is_accepted(github):
    receipt = acceptance.collect_acceptance("acme/repo", PR)
    assert receipt["ok"] is True
    assert receipt["classification"] == "success"
    assert receipt["head_sha"] == "a" * 40


def test_open_pr_without_required_checks_is_refused(github):
    github["pr_state"] = "OPEN"
    receipt = acceptance.collect_acceptance("acme/repo", PR)
    assert receipt["ok"] is False
    assert "No repository-required checks" in receipt["detail"]


def test_merged_pr_with_required_checks_still_needs_them(github):
    github["required"] = [{"context": "required", "app": {"databaseId": 1}}]
    receipt = acceptance.collect_acceptance("acme/repo", PR)
    assert receipt["ok"] is False
    assert receipt["classification"] == "missing"
