"""Plan new Forge coding sessions and independent review without changing live profiles."""

import argparse
import json
from pathlib import Path


def implementation(scope: str, verification: str, hazard: str, evidence: str) -> dict:
    if not evidence.strip():
        raise ValueError("task evidence is required")
    level = "medium"
    factors = []
    if scope == "multi":
        level = "high"
        factors.append("multiple components")
    elif scope == "system":
        level = "xhigh"
        factors.append("system-wide behavior")
    if verification == "integration":
        if level == "medium":
            level = "high"
        factors.append("integration verification")
    elif verification == "uncertain":
        level = "xhigh"
        factors.append("hard-to-reproduce verification")
    if hazard != "none":
        level = "xhigh"
        factors.append(f"{hazard} risk")
    if not factors:
        factors.append("local change with a direct check")
    return {
        "model": "gpt-6-sol",
        "effort": level,
        "reason": f"{'; '.join(factors)}: {evidence.strip()}",
        "launch_args": ["--runtime", "codex", "--model", "gpt-6-sol", "--effort", level],
    }


def review(repo: Path, charter: Path, artifact: Path, out: Path, reviewer: str) -> dict:
    repo = repo.resolve()
    out = out.resolve()
    if reviewer not in {"codex", "claude"}:
        raise ValueError("reviewer must be codex or claude")
    if not repo.is_dir() or not charter.is_file() or not artifact.is_file():
        raise ValueError("review needs an existing checkout, charter, and artifact")
    if out == repo or repo in out.parents:
        raise ValueError("review output must stay outside the implementation checkout")
    if reviewer == "codex":
        model, effort = "gpt-6-sol", "high"
    else:
        model, effort = "claude-opus-5-5", "medium"
    return {
        "reviewer": reviewer,
        "model": model,
        "effort": effort,
        "status": "planned",
        "required_isolation": "fresh read-only",
        "dispatch_args": [
            "dispatch", "--family", reviewer, "--model", model, "--effort", effort,
            "--repo", str(repo), "--charter", str(charter.resolve()),
            "--artifact", str(artifact.resolve()), "--out", str(out),
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    launch = commands.add_parser("implementation")
    launch.add_argument("--scope", choices=("local", "multi", "system"), required=True)
    launch.add_argument("--verification", choices=("direct", "integration", "uncertain"), required=True)
    launch.add_argument("--hazard", choices=("none", "security", "auth", "data-loss", "migration"), required=True)
    launch.add_argument("--evidence", required=True)
    review_parser = commands.add_parser("review")
    review_parser.add_argument("--repo", type=Path, required=True)
    review_parser.add_argument("--charter", type=Path, required=True)
    review_parser.add_argument("--artifact", type=Path, required=True)
    review_parser.add_argument("--out", type=Path, required=True)
    review_parser.add_argument("--reviewer", choices=("codex", "claude"), default="codex")
    args = parser.parse_args()
    try:
        result = (implementation(args.scope, args.verification, args.hazard, args.evidence)
                  if args.command == "implementation"
                  else review(args.repo, args.charter, args.artifact, args.out, args.reviewer))
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
