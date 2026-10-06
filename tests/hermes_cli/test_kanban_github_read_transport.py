"""kanban.github_read_transport: an opt-in, exact-repository remote ``gh`` read.

The real ``_api`` runs against a fake ``ssh`` that executes the quoted remote script with
``/bin/sh`` and a fake remote ``gh`` whose ``auth token --user`` knows one account. A local
``gh`` on PATH records any fall-through, so every fail-closed case proves it never ran.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli import kanban_pr_acceptance as acc

pytestmark = pytest.mark.platforms("posix")

TOKEN = "remote-only-token-sentinel"
PR747 = "https://github.com/Epoch-ML/zerg/pull/747"
PR748 = "https://github.com/Epoch-ML/zerg/pull/748"
QUERY = acc._acceptance_query("Epoch-ML/zerg", 748)  # the one routable GraphQL document

SSH = """#!{py}
import json, os, sys
log = {log!r}
argv = sys.argv[1:]
with open(log, "a") as f:
    f.write(json.dumps({{"argv": argv, "env_has_token": any(k in os.environ for k in ("GH_TOKEN", "GITHUB_TOKEN"))}}) + "\\n")
host = argv[argv.index("--") + 1]
if host != "mbp":
    sys.stderr.write("ssh: Could not resolve hostname\\n")
    sys.exit(255)
assert len(argv) == argv.index("--") + 3, argv
os.execv("/bin/sh", ["sh", "-c", argv[-1]])
"""

REMOTE_GH = """#!{py}
import json, os, sys
argv = sys.argv[1:]
with open({log!r}, "a") as f:
    f.write(json.dumps(argv) + "\\n")
if argv[:2] == ["auth", "token"]:
    if "GH_TOKEN" in os.environ or "GITHUB_TOKEN" in os.environ:
        sys.exit(9)  # the token lookup must not see an inherited token
    user = argv[argv.index("--user") + 1]
    if user == "empty-output":
        sys.exit(0)  # success with no token: the remote script must still refuse
    if user != "michaelluochen":
        sys.stderr.write("no account found\\n")
        sys.exit(1)
    print({token!r})
    sys.exit(0)
assert argv[0] == "api", argv
if os.environ.get("GH_TOKEN") != {token!r}:
    sys.stderr.write("gh: Not Found (HTTP 404)\\n")
    sys.exit(1)
endpoint = argv[argv.index("graphql") if "graphql" in argv else argv.index("GET") + 1]
if endpoint == "graphql":
    print(json.dumps({{"data": {{"repository": {{"pullRequest": {{
        "headRefOid": "b" * 40, "baseRefName": "development", "state": "OPEN",
        "baseRef": {{"branchProtectionRule": {{"requiredStatusChecks": [
            {{"context": "ci", "app": {{"databaseId": 1}}}}]}}}}}}}}}}}}))
    sys.exit(0)
if "/rules/branches/" in endpoint or "/statuses" in endpoint:
    print(json.dumps([[]]))
    sys.exit(0)
if "/check-runs" in endpoint:
    print(json.dumps([{{"total_count": 1, "check_runs": [{{
        "id": 1, "name": "ci", "head_sha": "b" * 40, "app": {{"id": 1}}, "status": "completed",
        "conclusion": "success", "html_url": "https://github.com/Epoch-ML/zerg/actions/runs/1"}}]}}]))
    sys.exit(0)
endpoint = endpoint.split("?", 1)[0]
number = int(endpoint.rsplit("/", 1)[1])
if number not in (747, 748):
    sys.stderr.write("gh: Not Found (HTTP 404)\\n")
    sys.exit(1)
print(json.dumps({{"html_url": "https://github.com/Epoch-ML/zerg/pull/%d" % number, "number": number,
                  "state": "open", "merged_at": None, "closed_at": None, "draft": False,
                  "base": {{"repo": {{"full_name": "Epoch-ML/zerg"}}, "ref": "development"}},
                  "head": {{"sha": ("a" if number == 747 else "b") * 40,
                           "ref": "feat/slack" if number == 747 else "feat/simulation"}}}}))
"""

LOCAL_GH = """#!/bin/sh
echo "$@" >> {log!r}
echo "gh: Not Found (HTTP 404)" >&2
exit 1
"""


def _script(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(0o755)
    return path


@pytest.fixture
def remote(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    bin_dir, remote_dir = tmp_path / "bin", tmp_path / "remote"
    bin_dir.mkdir()
    remote_dir.mkdir()
    logs = {k: tmp_path / f"{k}.log" for k in ("ssh", "remote", "local")}
    for log in logs.values():
        log.write_text("")
    _script(bin_dir / "ssh", SSH.format(py=sys.executable, log=str(logs["ssh"])))
    _script(bin_dir / "gh", LOCAL_GH.format(log=str(logs["local"])))
    gh = _script(remote_dir / "gh", REMOTE_GH.format(py=sys.executable, log=str(logs["remote"]), token=TOKEN))
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("HERMES_HOME", str(home))
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR"):
        monkeypatch.delenv(key, raising=False)
    route = {"ssh_host": "mbp", "gh_user": "michaelluochen", "gh": str(gh)}

    def configure(table, where=home):
        Path(where).mkdir(parents=True, exist_ok=True)
        (Path(where) / "config.yaml").write_text(json.dumps({"kanban": {"github_read_transport": table}}))

    def lines(name):
        return [json.loads(x) if name != "local" else x
                for x in logs[name].read_text().splitlines() if x]

    return {"home": home, "route": route, "configure": configure, "lines": lines, "tmp": tmp_path}


def test_no_route_keeps_the_local_command_byte_for_byte(remote, monkeypatch):
    seen = []
    real = subprocess.run
    monkeypatch.setattr(acc.subprocess, "run", lambda cmd, **kw: seen.append((cmd, kw["env"])) or real(cmd, **kw))
    with pytest.raises(acc._GateAuthError, match="HTTP 404"):
        acc._api("repos/Epoch-ML/zerg/pulls/747")
    assert seen == [(["gh", "api", "repos/Epoch-ML/zerg/pulls/747", "--hostname", "github.com"], None)]
    assert remote["lines"]("ssh") == []


def test_exact_repo_route_reads_on_the_remote_login_only(remote):
    remote["configure"]({"epoch-ml/ZERG": remote["route"]})  # case-insensitive key
    data = acc._api("repos/Epoch-ML/zerg/pulls/747")
    assert (data["html_url"], data["state"], data["head"]["ref"]) == (PR747, "open", "feat/slack")
    assert acc._api("repos/epoch-ml/Zerg/pulls/748")["number"] == 748
    ssh = remote["lines"]("ssh")
    assert len(ssh) == 2 and not any(entry["env_has_token"] for entry in ssh)
    assert TOKEN not in json.dumps(ssh) and remote["lines"]("local") == []
    assert ssh[0]["argv"][:10] == ["-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o",
                                   "ForwardAgent=no", "-o", "ClearAllForwardings=yes", "--"]
    calls = remote["lines"]("remote")
    assert ["auth", "token", "--hostname", "github.com", "--user", "michaelluochen"] in calls
    assert ["api", "--method", "GET", "repos/Epoch-ML/zerg/pulls/747", "--hostname", "github.com"] in calls
    assert TOKEN not in json.dumps(calls)


@pytest.mark.parametrize("endpoint", ["repos/Epoch-ML/zerg-x/pulls/1", "repos/Epoch-ML/zer/pulls/1",
                                      "repos/Other/zerg/pulls/1", "repos/Epoch-ML.evil/zerg/pulls/1"])
def test_sibling_and_prefix_repositories_are_not_routed(remote, endpoint):
    remote["configure"]({"Epoch-ML/zerg": remote["route"]})
    with pytest.raises(acc._GateAuthError):
        acc._api(endpoint)
    assert remote["lines"]("ssh") == [] and len(remote["lines"]("local")) == 1


@pytest.mark.parametrize("mutate", [
    lambda r: r.pop("gh"), lambda r: r.update(gh="gh"), lambda r: r.update(gh="/opt/../bin/gh"),
    lambda r: r.update(gh="/opt/homebrew/bin/gh;id"), lambda r: r.update(gh="/opt/homebrew/bin/ghx"),
    lambda r: r.update(gh="/opt/./bin/gh"), lambda r: r.update(ssh_host="-oProxyCommand=id"),
    lambda r: r.update(ssh_host="mbp main"), lambda r: r.update(ssh_host="mbp;id"),
    lambda r: r.update(ssh_host="user@mbp"), lambda r: r.update(gh_user="a;b"), lambda r: r.update(gh_user=""),
    lambda r: r.update(gh_user="-bad"), lambda r: r.update(command="id"), lambda r: r.update(gh_user=7),
])
def test_malformed_matching_route_fails_closed(remote, mutate):
    route = dict(remote["route"])
    mutate(route)
    remote["configure"]({"Epoch-ML/zerg": route})
    with pytest.raises(acc._GateAuthError, match="malformed"):
        acc._api("repos/Epoch-ML/zerg/pulls/747")
    assert remote["lines"]("ssh") == [] and remote["lines"]("local") == []


@pytest.mark.parametrize("table", ["dup", {"Epoch-ML/zerg": "mbp"}, {"EPOCH-ML/ZERG": ["mbp"]}])
def test_malformed_matching_entry_fails_closed(remote, table):
    if table == "dup":
        table = {"Epoch-ML/zerg": remote["route"], "epoch-ml/zerg": remote["route"]}
    remote["configure"](table)
    with pytest.raises(acc._GateAuthError, match="malformed"):
        acc._api("repos/Epoch-ML/zerg/pulls/747")
    assert remote["lines"]("ssh") == [] and remote["lines"]("local") == []


@pytest.mark.parametrize("config,match", [("kanban: [unclosed\n", "unreadable"),
                                          ("not a mapping", "mapping"), ('["Epoch-ML/zerg"]', "mapping")])
def test_config_that_cannot_prove_the_route_absent_fails_closed(remote, config, match):
    """An unreadable/unparseable config or a non-mapping table refuses every read: the route
    cannot be ruled out, so neither the local login nor ssh runs."""
    if config.startswith("kanban"):
        (remote["home"] / "config.yaml").write_text(config)
    else:
        remote["configure"](json.loads(config) if config.startswith("[") else config)
    with pytest.raises(acc._GateAuthError, match=match):
        acc._api("repos/Epoch-ML/zerg/pulls/747")
    with pytest.raises(acc._GateAuthError, match=match):
        acc._api("repos/Other/unrouted/pulls/1")
    assert remote["lines"]("ssh") == [] and remote["lines"]("local") == []


@pytest.mark.parametrize("config", [None, {"kanban": {}}, {"kanban": {"github_read_transport": {}}},
                                    {"kanban": {"github_read_transport": {"Other/repo": "ignored"}}}])
def test_missing_file_absent_key_or_unmatched_repo_keeps_the_local_login(remote, config):
    if config is not None:
        (remote["home"] / "config.yaml").write_text(json.dumps(config))
    with pytest.raises(acc._GateAuthError, match="HTTP 404"):
        acc._api("repos/Epoch-ML/zerg/pulls/747")
    assert remote["lines"]("ssh") == [] and len(remote["lines"]("local")) == 1


@pytest.mark.parametrize("user", ["someone-else", "empty-output"])
def test_missing_remote_token_never_falls_back(remote, user):
    remote["configure"]({"Epoch-ML/zerg": {**remote["route"], "gh_user": user}})
    with pytest.raises(acc._GateAuthError, match="no login"):
        acc._api("repos/Epoch-ML/zerg/pulls/747")
    calls = remote["lines"]("remote")
    assert [c[:2] for c in calls] == [["auth", "token"]] and remote["lines"]("local") == []


def test_ssh_failure_and_remote_denial_never_fall_back(remote):
    remote["configure"]({"Epoch-ML/zerg": {**remote["route"], "ssh_host": "unreachable"}})
    with pytest.raises(subprocess.CalledProcessError):
        acc._api("repos/Epoch-ML/zerg/pulls/747")
    remote["configure"]({"Epoch-ML/zerg": remote["route"]})
    with pytest.raises(acc._GateAuthError, match="HTTP 404"):
        acc._api("repos/Epoch-ML/zerg/pulls/999")
    assert remote["lines"]("local") == []


@pytest.mark.parametrize("endpoint,query", [
    ("repos/Epoch-ML/zerg/contents/README.md", None), ("repos/Epoch-ML/zerg/pulls/1/merge", None),
    ("repos/Epoch-ML/zerg/pulls/1?x=$(id)", None), ("repos/Epoch-ML/zerg/pulls/1;id", None),
    ("repos/Epoch-ML/zerg/git/refs", None), ("repos/Epoch-ML/zerg/pulls/1", "{viewer{login}}"),
])
def test_routed_reads_are_limited_to_the_acceptance_endpoints(remote, endpoint, query):
    remote["configure"]({"Epoch-ML/zerg": remote["route"]})
    with pytest.raises(ValueError):
        acc._api(endpoint, query=query)
    assert remote["lines"]("ssh") == [] and remote["lines"]("local") == []


@pytest.mark.parametrize("query", [
    QUERY[:-1] + ' other:repository(owner:"Other",name:"secret"){id}}',            # second repository
    QUERY[:-1] + " viewer{login}}",                                               # extra top-level root
    QUERY.replace("{repository(", "{r:repository(", 1),                           # aliased root
    QUERY.replace("pullRequest(number:748){", "pr:pullRequest(number:748){", 1),  # aliased field
    QUERY.replace("headRefOid ", "headRefOid body ", 1),                         # arbitrary field
    QUERY.replace("app{databaseId}", "app{databaseId name}", 1),                  # nested extra field
    QUERY.replace("state\n", "state author{login}\n", 1),                         # extra subtree
    QUERY.replace("pullRequest(number:748)", "issue(number:748)", 1),             # different field
    QUERY + " mutation{addStar(input:{starrableId:\"x\"}){clientMutationId}}",     # second operation
    QUERY + " subscription{x}", "query Q" + QUERY, " " + QUERY, QUERY + "\n",     # operations/whitespace
    QUERY.replace("\n            ", " ", 1),                                       # reformatted
    QUERY[:-1], QUERY + "}",                                                     # unbalanced
    QUERY.replace("748", "0", 1), QUERY.replace("748", str(2**31), 1),            # bad numbers
    QUERY.replace("748", "748.0", 1), QUERY.replace("748", "-748", 1), QUERY.replace("748", "0748", 1),
    QUERY.replace("748", "$n", 1), QUERY.replace("748", "748,first:1", 1),
])
def test_routed_graphql_must_be_the_exact_acceptance_query(remote, query):
    remote["configure"]({"Epoch-ML/zerg": remote["route"]})
    with pytest.raises(ValueError):
        acc._api("graphql", query=query, repo="Epoch-ML/zerg")
    assert remote["lines"]("ssh") == [] and remote["lines"]("local") == []


@pytest.mark.parametrize("query,repo", [
    (QUERY, "Epoch-ML/other"), (QUERY.replace('"zerg"', '"zerg-x"', 1), "Epoch-ML/zerg"),
    ("mutation{addComment(input:{}){clientMutationId}}", "Epoch-ML/zerg"),
    ("subscription{x}", "Epoch-ML/zerg"), ("{viewer{login}}", "Epoch-ML/zerg"), (QUERY, "Epoch-ML/zerg;id"),
])
@pytest.mark.parametrize("routed", [True, False])
def test_declared_graphql_repository_mismatch_fails_consistently(remote, query, repo, routed):
    if routed:
        remote["configure"]({"Epoch-ML/zerg": remote["route"]})
    with pytest.raises(ValueError):
        acc._api("graphql", query=query, repo=repo)
    assert remote["lines"]("ssh") == [] and remote["lines"]("local") == []


def test_routed_graphql_cannot_paginate(remote):
    remote["configure"]({"Epoch-ML/zerg": remote["route"]})
    with pytest.raises(ValueError):
        acc._api("graphql", query=QUERY, repo="Epoch-ML/zerg", paginate=True)
    assert remote["lines"]("ssh") == []


@pytest.mark.parametrize("query", [QUERY, "{viewer{login}}"])
def test_graphql_without_repo_keeps_the_historical_local_command(remote, monkeypatch, query):
    remote["configure"]({"Epoch-ML/zerg": remote["route"]})  # even with a route for that repo
    seen = []
    real = subprocess.run
    monkeypatch.setattr(acc.subprocess, "run", lambda cmd, **kw: seen.append((cmd, kw["env"])) or real(cmd, **kw))
    with pytest.raises(acc._GateAuthError, match="HTTP 404"):
        acc._api("graphql", query=query)
    assert seen == [(["gh", "api", "graphql", "--hostname", "github.com", "-f", "query=" + query], None)]
    assert remote["lines"]("ssh") == []


def test_graphql_read_is_routed_with_its_explicit_repository(remote):
    remote["configure"]({"Epoch-ML/zerg": remote["route"]})
    data = acc._api("graphql", query=QUERY, repo="epoch-ml/zerg")
    assert data["data"]["repository"]["pullRequest"]["headRefOid"] == "b" * 40
    assert ["api", "graphql", "--hostname", "github.com", "-f", "query=" + QUERY] in remote["lines"]("remote")
    with pytest.raises(ValueError):
        acc._api("repos/Epoch-ML/zerg/pulls/747", repo="Epoch-ML/other")


def test_completion_acceptance_reads_every_endpoint_through_the_route(remote):
    remote["configure"]({"Epoch-ML/zerg": remote["route"]})
    receipt = acc.collect_acceptance(PR748, None)
    assert (receipt["ok"], receipt["classification"], receipt["head_sha"]) == (True, "success", "b" * 40)
    endpoints = [c[c.index("GET") + 1] if "GET" in c else c[1] for c in remote["lines"]("remote") if c[0] == "api"]
    assert endpoints == ["graphql", "repos/Epoch-ML/zerg/rules/branches/development?per_page=100",
                         "repos/Epoch-ML/zerg/commits/" + "b" * 40 + "/check-runs?per_page=100&filter=latest",
                         "repos/Epoch-ML/zerg/commits/" + "b" * 40 + "/statuses?per_page=100",
                         "repos/Epoch-ML/zerg/pulls/748"]
    assert remote["lines"]("local") == []


def test_assignee_reads_use_only_that_profile_config(remote, monkeypatch):
    profile = remote["tmp"] / "profiles" / "forge"
    profile.mkdir(parents=True)
    homes = []
    monkeypatch.setattr(acc, "_gh_env", lambda home: homes.append(home))
    remote["configure"]({"Epoch-ML/zerg": remote["route"]})  # root route only
    with pytest.raises(acc._GateAuthError, match="HTTP 404"):
        acc._api("repos/Epoch-ML/zerg/pulls/747", profile_home=str(profile))
    assert homes == [str(profile)] and remote["lines"]("ssh") == []  # no silent inheritance
    remote["configure"]({"Epoch-ML/zerg": remote["route"]}, where=profile)
    remote["configure"]({}, where=remote["home"])
    assert acc._api("repos/Epoch-ML/zerg/pulls/748", profile_home=str(profile))["number"] == 748
    with pytest.raises(acc._GateAuthError):
        acc._api("repos/Epoch-ML/zerg/pulls/748")  # the profile's route never applies to root reads
    assert len(remote["lines"]("ssh")) == 1


# --- Authorized resume through the real routed _api: the guards decide, not the transport ---

@pytest.fixture
def board(remote, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: remote["tmp"])
    for key in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_TASK"):
        monkeypatch.delenv(key, raising=False)
    import hermes_cli.config as config
    import hermes_cli.profiles as profiles
    monkeypatch.setattr(config, "load_config", lambda: {"kanban": {}})
    monkeypatch.setattr(profiles, "profile_exists", lambda name: True)
    monkeypatch.setattr(kbd, "_memory_pressure_level", lambda: "unknown")
    remote["configure"]({"Epoch-ML/zerg": remote["route"]})
    kb.init_db()

    def make(guarded, contract=PR748):
        with kbc.connect() as conn:
            tid = kb.create_task(conn, title="Continue PR", assignee="forge", completion_contract=contract)
            pr = kb.add_comment(conn, tid, author="default", body="Guarding " + " ".join(guarded))
            auth = kb.add_comment(conn, tid, author="default", body="Resume the contract PR.")
            conn.execute("UPDATE task_comments SET created_at=? WHERE id=?", (int(time.time()) - 20, pr))
        return tid, auth
    return make


def _resume(tid, auth):
    from hermes_cli import kanban_pr_resume
    with kbc.connect() as conn:
        result = kanban_pr_resume.recover_authorized_pr_resume(
            conn, tid, auth, actor="support", reason="Operator resume", dry_run=True)
        recorded = conn.execute("SELECT COUNT(*) FROM task_events WHERE task_id=? AND kind='authorized_pr_resume'",
                                (tid,)).fetchone()[0]
        return result, recorded, kbd.check_respawn_guard(conn, tid)


def test_open_sibling_guard_still_refuses_contract_mismatch(board):
    result, recorded, guard = _resume(*board([PR747]))
    assert result.status == "contract_mismatch" and [p["url"] for p in result.prs] == [PR747]
    assert recorded == 0 and guard == "active_pr"


def test_true_contract_pr_open_verifies_dry_run(board):
    result, recorded, _ = _resume(*board([PR748]))
    assert result.status == "verified" and result.prs[0]["head_ref"] == "feat/simulation"
    assert recorded == 0


def test_extra_open_sibling_is_refused(board):
    result, recorded, guard = _resume(*board([PR747, PR748]))
    assert result.status == "ambiguous_open_pr" and recorded == 0 and guard == "active_pr"
