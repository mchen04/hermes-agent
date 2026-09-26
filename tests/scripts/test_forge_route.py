"""Forge session routing keeps task risk and reviewer isolation visible."""

import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest


_MODULE_PATH = Path(__file__).resolve().parents[2] / "forge-policy" / "route.py"
route = ModuleType("forge_route")
route.__file__ = str(_MODULE_PATH)
exec(compile(_MODULE_PATH.read_bytes(), str(_MODULE_PATH), "exec"), route.__dict__)


def test_new_session_effort_tracks_scope_and_verification():
    simple = route.implementation("local", "direct", "none", "One label fix; visual check passes")
    difficult = route.implementation("multi", "integration", "none", "Two adapters share a retry path")
    assert simple["effort"] == "medium"
    assert difficult["effort"] == "high"
    assert simple["launch_args"][-2:] == ["--effort", "medium"]
    assert difficult["launch_args"][-2:] == ["--effort", "high"]
    assert "label fix" in simple["reason"]
    assert "retry path" in difficult["reason"]


def test_sensitive_or_uncertain_work_uses_top_supported_effort():
    for scope, verification, hazard in (
        ("local", "direct", "security"),
        ("local", "uncertain", "none"),
        ("system", "direct", "none"),
    ):
        assert route.implementation(scope, verification, hazard, "Concrete task evidence")["effort"] == "xhigh"
    with pytest.raises(ValueError, match="evidence"):
        route.implementation("local", "direct", "none", " ")


def test_review_defaults_to_fresh_read_only_sol_and_keeps_claude_route(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    charter = tmp_path / "charter.txt"
    artifact = tmp_path / "diff.txt"
    charter.write_text("Review correctness")
    artifact.write_text("One file diff")
    out = tmp_path / "review"
    run = subprocess.run(
        [sys.executable, str(_MODULE_PATH), "review", "--repo", str(repo),
         "--charter", str(charter), "--artifact", str(artifact), "--out", str(out)],
        capture_output=True, text=True, check=True,
    )
    sol = json.loads(run.stdout)
    claude = route.review(repo, charter, artifact, out, "claude")
    assert (sol["model"], sol["effort"], sol["status"], sol["required_isolation"]) == (
        "gpt-6-sol", "high", "planned", "fresh read-only")
    assert sol["dispatch_args"][1:7] == ["--family", "codex", "--model", "gpt-6-sol", "--effort", "high"]
    assert claude["dispatch_args"][1:7] == ["--family", "claude", "--model", "claude-opus-5-5", "--effort", "medium"]
    with pytest.raises(ValueError, match="outside"):
        route.review(repo, charter, artifact, repo / "review", "codex")


def test_deployable_route_matches_tested_source():
    patch = (_MODULE_PATH.parent / "skills-source.patch").read_text()
    header = "diff --git a/skills/supervise/scripts/forge_route.py b/skills/supervise/scripts/forge_route.py\n"
    section = patch.split(header, 1)[1]
    lines = section.splitlines(keepends=True)
    start = lines.index("+++ b/skills/supervise/scripts/forge_route.py\n")
    assert lines[start - 1] == "--- /dev/null\n"
    assert lines[start + 1].startswith("@@ ")
    embedded = "".join(line[1:] for line in lines[start + 2:] if line.startswith("+"))
    assert embedded == _MODULE_PATH.read_text()
