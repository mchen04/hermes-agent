"""Two lifecycle invariants, using real SQLite and a local GitHub HTTP contract."""
import base64
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_connect import connect


@pytest.fixture
def github(tmp_path, monkeypatch):
    state = {"conclusion": "success", "head": "a" * 40, "reads": 0, "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"].append(self.path)
            sha = state["head"]
            if self.path == "/graphql":
                value = {"data": {"repository": {"pullRequest": {
                    "headRefOid": sha, "headRefName": "feature/fix", "baseRefName": "main", "state": "OPEN",
                    "baseRef": {"branchProtectionRule": {"requiredStatusChecks": [
                        {"context": "required", "app": {"databaseId": 1}}]
                        if state.get("required", True) else []}}}}}}
            elif "/rules/branches/" in self.path:
                value = [[]]
            elif "/check-runs" in self.path:
                run = {"id": 42, "name": "required", "head_sha": sha,
                       "app": {"id": 1}, "status": "in_progress" if state["conclusion"] == "pending" else "completed", "conclusion": state["conclusion"],
                       "html_url": "https://github.com/acme/repo/actions/runs/42"}
                if state.get("stale"):
                    run["head_sha"] = "b" * 40
                runs = [] if state.get("missing") else [run]
                value = [{"total_count": 100 + len(runs), "check_runs": [
                    {**run, "id": 1000 + i, "name": "optional", "conclusion": "skipped"}
                    for i in range(100)]}, {"total_count": 100 + len(runs), "check_runs": runs}]
                if state.get("empty"):
                    value = [{"total_count": 0, "check_runs": []}]
                if state.get("race"):
                    state["race"]()
                if state.get("head_change"):
                    state["head"] = "b" * 40
            elif "/statuses" in self.path:
                value = [[]]
            elif "/check-suites" in self.path:
                suites = [] if not state.get("suite") else [{"id": 9, "head_sha": sha,
                    "app": {"id": 1, "name": "CI", "slug": "github-actions"},
                    "status": "completed" if state["suite"] != "pending" else "queued",
                    "conclusion": None if state["suite"] == "pending" else state["suite"]}]
                if state.get("unreported_app"):
                    suites.append({"id": 10, "head_sha": sha, "app": {"id": 2, "name": "Optional integration", "slug": "optional-integration"},
                        "status": "queued", "conclusion": None, "latest_check_runs_count": 0,
                        "created_at": "2026-09-21T19:26:40Z", "updated_at": "2026-09-21T19:26:40Z"})
                value = [{"total_count": len(suites), "check_suites": suites}]
            elif "/actions/workflows" in self.path:
                workflows = [{"id": 1, "state": "active", "path": ".github/workflows/check.yml"}] if state.get("workflow") else []
                value = [{"total_count": len(workflows), "workflows": workflows}]
            elif "/contents/.github/workflows/" in self.path:
                definition = state.get("workflow_definition", "on: [push, pull_request]\njobs: {}")
                value = {"encoding": "base64", "content": base64.b64encode(definition.encode()).decode()}
            elif "/pulls/" in self.path:
                number = int(self.path.rsplit("/", 1)[1])
                pr_state = state.get("pull_states", {}).get(number, "open")
                value = {"head": {"sha": sha}, "base": {"ref": "main"}, "number": number,
                         "state": "closed" if pr_state in {"closed", "merged"} else "open",
                         "merged": pr_state == "merged"}
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    shim = tmp_path / "bin"
    shim.mkdir()
    gh = shim / "gh"
    gh.write_text(f"#!{sys.executable}\nimport sys,urllib.request\n"
                  f"u='http://127.0.0.1:{server.server_port}/'+sys.argv[2]\n"
                  "print(urllib.request.urlopen(u).read().decode())\n")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(shim) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.platforms("linux")
def test_pr_completion_requires_current_required_evidence(github):
    with connect() as conn:
        for conclusion in ("failure", "pending", "cancelled", "timed_out", "action_required", "neutral", "skipped", None, "success"):
            github.update(conclusion=conclusion, head="a" * 40)
            tid = kb.create_task(conn, title="Publish", completion_contract="acme/repo")
            ok = kb.complete_task(conn, tid, result="done", metadata={"published_pr": "https://github.com/acme/repo/pull/7"})
            assert ok is (conclusion == "success")
            task = kb.get_task(conn, tid)
            assert (task.status == "done") is ok
            receipts = [json.loads(r[0]) for r in conn.execute(
                "SELECT payload FROM task_events WHERE task_id=? AND kind='pr_acceptance'", (tid,))]
            assert receipts and receipts[-1]["head_sha"] == "a" * 40
            if not ok:
                assert task.status in {"running", "ready", "blocked", "review"}
                assert "retry" in receipts[-1]["recovery"]
                assert receipts[-1]["checks"][0]["id"] == 42
        for fault in ("missing", "stale", "head_change"):
            github.update(conclusion="success", head="a" * 40)
            github[fault] = True
            tid = kb.create_task(conn, title=fault, completion_contract="acme/repo")
            assert not kb.complete_task(conn, tid, result="done", metadata={"published_pr": "https://github.com/acme/repo/pull/7"})
            assert kb.get_task(conn, tid).status != "done"
            github.pop(fault)
        # Omission and a sibling repository cannot downgrade the stored declaration.
        tid = kb.create_task(conn, title="publish", completion_contract="acme/repo")
        assert not kb.complete_task(conn, tid, summary="local green")
        assert not kb.complete_task(conn, tid, result="done", metadata={"published_pr": "https://github.com/other/repo/pull/7"})
        before = len(github["requests"])
        local = kb.create_task(conn, title="local", completion_contract="local-only")
        assert kb.complete_task(conn, local, summary="https://github.com/acme/repo/pull/7 is background context")
        assert len(github["requests"]) == before


@pytest.mark.platforms("linux")
def test_acceptance_receipts_and_terminal_write_share_run_ownership(github):
    with connect() as conn:
        for conclusion in ("success", "failure"):
            tid = kb.create_task(conn, title="race", completion_contract="acme/repo")
            owner = kb.claim_task(conn, tid)
            run_id = owner.current_run_id
            def reclaim():
                with connect() as rival:
                    assert kb.block_task(rival, tid, reason="Reassigned during acceptance")
                    assert kb.unblock_task(rival, tid)
                    github["replacement"] = kb.claim_task(rival, tid).current_run_id
            github.update(conclusion=conclusion, race=reclaim)
            assert not kb.complete_task(conn, tid, result="done", expected_run_id=run_id,
                metadata={"published_pr": "https://github.com/acme/repo/pull/7"})
            assert kb.get_task(conn, tid).current_run_id == github["replacement"]
            assert github["replacement"] != run_id
            assert kb.get_task(conn, tid).status != "done"
            assert conn.execute("SELECT count(*) FROM task_events WHERE task_id=? AND kind='pr_acceptance'", (tid,)).fetchone()[0] == 0
            github.pop("race")


def test_unprotected_pr_uses_actual_ci_and_distinguishes_absent_from_pending(github):
    github["required"] = False
    with connect() as conn:
        for conclusion in ("failure", "pending", "cancelled", "timed_out", "success"):
            github.update(conclusion=conclusion, head="a" * 40)
            tid = kb.create_task(conn, title="Publish reviewed changes", completion_contract="acme/repo")
            ok = kb.complete_task(conn, tid, result="done", metadata={"published_pr": "https://github.com/acme/repo/pull/7"})
            assert ok is (conclusion == "success")
            receipt = json.loads(conn.execute(
                "SELECT payload FROM task_events WHERE task_id=? AND kind='pr_acceptance' ORDER BY id DESC", (tid,)
            ).fetchone()[0])
            assert receipt["checks"] and receipt["head_sha"] == "a" * 40
        github.update(empty=True, workflow=True)
        tid = kb.create_task(conn, title="CI has not started", completion_contract="acme/repo")
        assert not kb.complete_task(conn, tid, result="done", metadata={"published_pr": "https://github.com/acme/repo/pull/7"})
        github["workflow"] = False
        assert kb.complete_task(conn, tid, result="done", metadata={"published_pr": "https://github.com/acme/repo/pull/7"})
        github.update(workflow=True, workflow_definition='on:\n  schedule:\n    - cron: "0 0 * * *"\n  workflow_dispatch:\njobs: {}')
        tid = kb.create_task(conn, title="Only scheduled/manual workflows", completion_contract="acme/repo")
        assert kb.complete_task(conn, tid, result="done", metadata={"published_pr": "https://github.com/acme/repo/pull/7"})


def test_unprotected_pr_cannot_hide_pending_or_failed_suites(github):
    github["required"] = False
    github["unreported_app"] = True
    with connect() as conn:
        for empty in (True, False):
            for suite in ("pending", "startup_failure", "failure", "success"):
                github.update(empty=empty, suite=suite)
                tid = kb.create_task(conn, title="Wait for all CI", completion_contract="acme/repo")
                assert kb.complete_task(conn, tid, result="done", metadata={"published_pr": "https://github.com/acme/repo/pull/7"}) is (suite == "success")
                receipt = json.loads(conn.execute(
                    "SELECT payload FROM task_events WHERE task_id=? AND kind='pr_acceptance' ORDER BY id DESC", (tid,)
                ).fetchone()[0])
                assert receipt["unreported_suites"][0]["app"] == "Optional integration"


@pytest.mark.parametrize("definition,completes", [
    ("on: release", True),
    ("on: issues", True),
    ("on: {push: {tags: ['v*']}}", True),
    ("on: {push: {branches: [main]}}", True),
    ("on: {push: {branches: ['feature/*']}}", False),
    ("on: {pull_request: {branches: [develop]}}", True),
    ("on: {pull_request: {branches: [main]}}", False),
    ("on: {pull_request: {branches: ['**', '!main']}}", True),
    ("on: {pull_request: {branches: ['**', '!main', main]}}", False),
    ("on: {pull_request: {types: [labeled]}}", True),
    ("on: {pull_request: {types: opened}}", False),
    ("on: {pull_request: {types: synchronize}}", False),
    ("on: {pull_request: {branches: develop}}", True),
    ("on: {pull_request: {branches-ignore: main}}", True),
])
def test_unrelated_workflows_do_not_require_pr_checks(github, definition, completes):
    github.update(required=False, empty=True, workflow=True, workflow_definition=definition+"\njobs: {}")
    with connect() as conn:
        tid = kb.create_task(conn, title="Publish without unrelated automation", completion_contract="acme/repo")
        assert kb.complete_task(conn, tid, result="done", metadata={"published_pr": "https://github.com/acme/repo/pull/7"}) is completes


# LOCAL-PATCH kanban-pr-supersede (2026-09-23): Pancake's card bound #69 (merged 09-21). The gate then rejected the
# real follow-up #73 ("Supply metadata.published_pr matching the persisted completion contract"), the worker passed
# #69 and the gate passed on #69's old head. A newer PR in the same repo replaces a bound PR that is no longer open,
# and the newer PR's own checks decide.
def _bound_task(conn, number=69):
    tid = kb.create_task(conn, title="Pancake hardening", completion_contract="acme/repo")
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET completion_contract=? WHERE id=?",
                     (f"https://github.com/acme/repo/pull/{number}", tid))
    return tid


def _receipts(conn, tid):
    return [json.loads(r[0]) for r in conn.execute(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='pr_acceptance' ORDER BY id", (tid,))]


def test_newer_pr_supersedes_a_merged_bound_pr(github):
    github.update(conclusion="success", pull_states={69: "merged"})
    with connect() as conn:
        tid = _bound_task(conn)
        assert kb.complete_task(conn, tid, result="done", metadata={"published_pr": "https://github.com/acme/repo/pull/73"})
        assert kb.get_task(conn, tid).completion_contract == "https://github.com/acme/repo/pull/73"
        receipt = _receipts(conn, tid)[-1]
        assert receipt["ok"] and receipt["pr_url"].endswith("/pull/73")
        assert receipt["superseded_pr"] == "https://github.com/acme/repo/pull/69"
        assert any(r.endswith("/pulls/73") for r in github["requests"])


def test_superseding_pr_must_pass_its_own_checks(github):
    github.update(conclusion="failure", pull_states={69: "merged"})
    with connect() as conn:
        tid = _bound_task(conn)
        assert not kb.complete_task(conn, tid, result="done", metadata={"published_pr": "https://github.com/acme/repo/pull/73"})
        assert kb.get_task(conn, tid).status != "done"
        assert _receipts(conn, tid)[-1]["classification"] == "failure"


@pytest.mark.parametrize("published, bound_state", [
    ("https://github.com/acme/repo/pull/73", "open"),     # the bound PR is still live: no swap to a sibling
    ("https://github.com/acme/repo/pull/60", "merged"),   # an older PR never replaces the bound one
    ("https://github.com/other/repo/pull/73", "merged"),  # another repository never replaces it
])
def test_supersede_refusals_keep_the_bound_pr(github, published, bound_state):
    github.update(conclusion="success", pull_states={69: bound_state})
    with connect() as conn:
        tid = _bound_task(conn)
        assert not kb.complete_task(conn, tid, result="done", metadata={"published_pr": published})
        assert kb.get_task(conn, tid).completion_contract == "https://github.com/acme/repo/pull/69"
        assert kb.get_task(conn, tid).status != "done"
        if bound_state == "open":
            assert "still open" in _receipts(conn, tid)[-1]["detail"]
