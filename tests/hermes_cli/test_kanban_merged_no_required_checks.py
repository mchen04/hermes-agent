"""LOCAL-PATCH kanban-merged-no-required-checks: a merged PR is the acceptance
when the repository requires no checks; an open PR there is still refused."""
import pytest

from hermes_cli import kanban_pr_acceptance as acceptance

PR = "https://github.com/acme/repo/pull/7"


@pytest.fixture
def github(monkeypatch):
    state = {"pr_state": "MERGED", "required": [], "runs": [], "repo": "acme/repo", "number": 7,
             "pull": {}, "reads": []}

    def fake_api(endpoint, *, query=None, paginate=False, profile_home=None, repo=None):
        state["reads"].append(endpoint)
        if endpoint == "graphql":
            assert repo == state["repo"]  # GraphQL acceptance names its repository explicitly
            rule = {"requiredStatusChecks": state["required"]} if state["required"] else None
            return {"data": {"repository": {"pullRequest": {
                "headRefOid": "a" * 40, "baseRefName": "main", "state": state["pr_state"],
                "baseRef": {"branchProtectionRule": rule}}}}}
        if "/rules/branches/" in endpoint or "/statuses" in endpoint:
            return [[]]
        if "/check-runs" in endpoint:
            return [{"total_count": len(state["runs"]), "check_runs": state["runs"]}]
        if "/pulls/" in endpoint:
            merged = state["pr_state"] == "MERGED"
            owner = state["repo"].split("/")[0]
            return {"number": state["number"], "head": {"sha": "a" * 40}, "merged": merged,
                    "state": "closed" if merged else "open",
                    "base": {"ref": "main", "repo": {"full_name": state["repo"], "owner": {"login": owner}}},
                    **state["pull"]}
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


# LOCAL-PATCH kanban-advisory-checks: on mchen04's repositories required checks are advisory too.
MINE = "https://github.com/mchen04/my-ai-skills/pull/164"
REQUIRED = [{"context": "required", "app": {"databaseId": 1}}]


def _run(conclusion, status="completed"):
    return {"id": 9, "name": "required", "head_sha": "a" * 40, "status": status,
            "conclusion": conclusion, "app": {"id": 1}, "html_url": "https://github.com/x/y/runs/9"}


@pytest.fixture
def mine(github):
    github.update(repo="mchen04/my-ai-skills", number=164, required=REQUIRED)
    return github


def _mine():
    return acceptance.collect_acceptance("mchen04/my-ai-skills", MINE)


@pytest.mark.parametrize("pr_state", ["MERGED", "OPEN"])
@pytest.mark.parametrize("run,observed", [
    (_run("failure"), "failure"), (_run(None, status="in_progress"), "pending"), (None, "missing")])
def test_mchen04_required_checks_are_advisory_and_receipts_stay_honest(mine, pr_state, run, observed):
    mine.update(pr_state=pr_state, runs=[run] if run else [])
    receipt = _mine()
    assert receipt["ok"] is True and receipt["classification"] == "advisory"
    assert receipt["required"] == [{"context": "required", "app_id": 1}]
    assert [c["classification"] for c in receipt["checks"]] == [observed]
    state = "merged" if pr_state == "MERGED" else "open"
    assert receipt["detail"].startswith(f"PR {state} at head {'a' * 40}; checks on mchen04")
    assert f"required {observed}" in receipt["detail"]


def test_mchen04_passing_required_check_reads_as_before(mine):
    mine["runs"] = [_run("success")]
    receipt = _mine()
    assert receipt["ok"] is True and receipt["classification"] == "success" and "advisory_owner" not in receipt


def test_mchen04_open_pr_without_required_checks_is_still_refused(mine):
    mine.update(pr_state="OPEN", required=[])
    receipt = _mine()
    assert receipt["ok"] is False and "No repository-required checks" in receipt["detail"]


@pytest.mark.parametrize("pull,classification", [
    ({"head": {"sha": "b" * 40}}, "stale"),
    ({"base": {"ref": "release", "repo": {"full_name": "mchen04/my-ai-skills", "owner": {"login": "mchen04"}}}},
     "stale"),
    ({"state": "closed", "merged": False}, "stale"),
    ({"base": {"ref": "main", "repo": {"full_name": "Epoch-ML/my-ai-skills", "owner": {"login": "Epoch-ML"}}}},
     "failure"),
    ({"number": 165}, "failure"),
], ids=["head-moved", "base-moved", "closed-unmerged", "transferred", "other-number"])
def test_mchen04_identity_and_head_still_gate(mine, pull, classification):
    mine.update(pr_state="OPEN", runs=[_run("failure")], pull=pull)
    receipt = _mine()
    assert receipt["ok"] is False and receipt["classification"] == classification


def test_mchen04_merged_claim_needs_github_to_say_merged(mine):
    mine.update(runs=[_run("failure")], pull={"state": "open", "merged": False})
    receipt = _mine()
    assert receipt["ok"] is False and receipt["classification"] == "failure"
    assert "Checks stayed a gate" in receipt["detail"]


@pytest.mark.parametrize("repo", ["Epoch-ML/zerg", "mchen04x/repo", "xmchen04/repo"])
@pytest.mark.parametrize("pr_state", ["MERGED", "OPEN"])
def test_other_owners_keep_required_checks_as_a_gate(github, repo, pr_state):
    github.update(repo=repo, required=REQUIRED, pr_state=pr_state, runs=[_run("failure")])
    receipt = acceptance.collect_acceptance(repo, f"https://github.com/{repo}/pull/7")
    assert receipt["ok"] is False and receipt["classification"] == "failure" and "detail" not in receipt


def test_completion_boundary_records_the_advisory_receipt(mine, tmp_path, monkeypatch):
    import json

    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    kb.init_db()
    mine["runs"] = [_run("failure")]
    with connect() as conn:
        tid = kb.create_task(conn, title="Publish", completion_contract="mchen04/my-ai-skills")
        assert not kb.complete_task(conn, tid, result="done", metadata={
            "published_pr": "https://github.com/mchen04/other/pull/164"})  # a sibling repo never binds
        assert kb.complete_task(conn, tid, result="done", metadata={"published_pr": MINE})
        assert kb.get_task(conn, tid).status == "done"
        assert kb.get_task(conn, tid).completion_contract == MINE
        receipt = json.loads(conn.execute("SELECT payload FROM task_events WHERE task_id=? AND kind='pr_acceptance' "
                                          "ORDER BY id DESC LIMIT 1", (tid,)).fetchone()[0])
        assert receipt["classification"] == "advisory" and receipt["checks"][0]["conclusion"] == "failure"
        mine["repo"] = "Epoch-ML/zerg"
        other = kb.create_task(conn, title="Publish", completion_contract="Epoch-ML/zerg")
        assert not kb.complete_task(conn, other, result="done", metadata={
            "published_pr": "https://github.com/Epoch-ML/zerg/pull/164"})
        assert kb.get_task(conn, other).status != "done"
