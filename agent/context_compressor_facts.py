from __future__ import annotations

import contextlib
import json
import re
from typing import Any

_TOOL_FACTS_HEADING = "## Latest Tool Facts (verbatim)"
_TOOL_FACTS_MAX_RESULTS = 8
_TOOL_FACTS_PER_RESULT = 6
# Command runners only: their output is where PR state, exit codes and test totals live. Other
# tools' JSON (clarify answers, skill lists) is not evidence of that kind and may be private.
_TOOL_FACT_TOOLS = frozenset({"terminal", "execute_code", "process"})
_TOOL_FACT_KEYS = frozenset({
    "state", "merged", "mergedat", "merged_at", "mergestatestatus", "conclusion", "status",
    "exit_code", "exitcode", "returncode", "exit_status", "success", "passed", "failed", "errors",
    "tests_passed", "tests_failed", "head_sha", "sha", "commit"})
_TOOL_FACT_LINE_RE = re.compile(
    r"(?i)(\b\d+ (?:passed|failed|errors?|skipped|xfailed|xpassed|tests? (?:passed|failed))\b"
    r"|\bRan \d+ tests?\b|^\s*(?:OK|FAILED)\b(?: \(.*\))?\s*$"
    r"|\bexit(?:ed)?(?: with)?(?: code| status)?\s*[:=]?\s*-?\d+\b"
    r"|\"?state\"?\s*[:=]\s*\"?(?:open|closed|merged)\b|\bmerged(?:At|_at)?\"?\s*[:=]"
    r"|(?-i:\b(?:MERGED|CLOSED)\b)|\bconclusion\"?\s*[:=]|\bTests?:\s*\d+)")


def _configured_summary_language() -> str:
    """``compression.summary_language`` from the active profile's config ("" = upstream rule)."""
    with contextlib.suppress(Exception):
        from hermes_cli.config import load_config
        cfg = (load_config() or {}).get("compression") or {}
        value = cfg.get("summary_language") if isinstance(cfg, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _json_fact_pairs(obj: Any, out: list, depth: int = 0) -> None:
    """``key=value`` for scalar fact keys in a parsed JSON tool result (bounded walk)."""
    if depth > 3 or len(out) >= _TOOL_FACTS_PER_RESULT:
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, (dict, list)):
                _json_fact_pairs(value, out, depth + 1)
            elif str(key).lower() in _TOOL_FACT_KEYS and value not in (None, ""):
                out.append(f"{key}={str(value)[:120]}")
            if len(out) >= _TOOL_FACTS_PER_RESULT:
                return
    elif isinstance(obj, list):
        for item in obj[:20]:
            _json_fact_pairs(item, out, depth + 1)


def _tool_result_facts(text: str) -> list:
    facts: list = []
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, (dict, list)):
        _json_fact_pairs(parsed, facts)
        nested = parsed.get("output") if isinstance(parsed, dict) else None
        text = nested if isinstance(nested, str) else ""
        try:
            inner = json.loads(text) if text.lstrip()[:1] in ("{", "[") else None
        except (TypeError, ValueError):
            inner = None
        if isinstance(inner, (dict, list)):  # e.g. `gh pr view --json state,mergedAt`
            _json_fact_pairs(inner, facts)
            text = ""
    for line in text.splitlines():
        if len(facts) >= _TOOL_FACTS_PER_RESULT:
            break
        line = line.strip()
        if line and _TOOL_FACT_LINE_RE.search(line):
            facts.append(line[:200])
    return list(dict.fromkeys(facts))


def latest_tool_facts(turns: list[dict[str, Any]]) -> list[str]:
    """One redacted line per recent tool result that states a key fact (state, merged, exit
    code, test totals), newest ``_TOOL_FACTS_MAX_RESULTS`` results, oldest first."""
    from agent.context_compressor import (_content_text_for_contains, _redact_compaction_text, _tool_calls_by_id)

    names = _tool_calls_by_id(turns)
    lines: list = []
    for msg in reversed(turns):
        if len(lines) >= _TOOL_FACTS_MAX_RESULTS:
            break
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        name = names.get(str(msg.get("tool_call_id") or ""), ("tool", ""))[0] or "tool"
        if name not in _TOOL_FACT_TOOLS:
            continue
        text = _content_text_for_contains(msg.get("content"))
        if text.startswith(f"[{name}]") and "\n" not in text.strip():
            # Already pruned to a one-line stub (which carries its facts): keep it whole.
            lines.append(_redact_compaction_text(f"- {text.strip()[:600]}"))
            continue
        facts = _tool_result_facts(text)
        if facts:
            lines.append(_redact_compaction_text(f"- [{name}] " + "; ".join(facts)))
    return list(reversed(lines))


def with_tool_facts_section(summary: str, facts: list[str]) -> str:
    """Replace any earlier facts section with ``facts`` (no-op when there are none)."""
    if not facts:
        return summary
    summary = re.sub(rf"(?ms)^{re.escape(_TOOL_FACTS_HEADING)}\s*\n.*?(?=^## |\Z)", "", summary).rstrip()
    return f"{summary}\n\n{_TOOL_FACTS_HEADING}\n" + "\n".join(facts) + "\n"


def user_turn_reply_status(messages: list[dict[str, Any]], window: list[dict[str, Any]], is_synthetic) -> tuple:
    """``(unanswered, answered_later)`` as sets of ``id()`` of real user turns in ``window``.
    A turn is answered by the next assistant text reply (a row without tool calls) anywhere in
    ``messages``; ``answered_later`` = that reply lies outside ``window`` (kept in the tail)."""
    from agent.context_compressor import (_content_text_for_contains)

    in_window = {id(m) for m in window}
    pending: list = []
    answered_later: set = set()
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "user" and not is_synthetic(msg):
            pending.append(id(msg))
        elif (role == "assistant" and pending and not msg.get("tool_calls")
              and _content_text_for_contains(msg.get("content")).strip()):
            if id(msg) not in in_window:
                answered_later.update(pending)
            pending = []
    return set(pending) & in_window, answered_later & in_window


def unanswered_user_turn_ids(messages: list[dict[str, Any]], is_synthetic) -> set:
    """``id()`` of each real user turn no later assistant text reply followed in ``messages``."""
    return user_turn_reply_status(messages, messages, is_synthetic)[0]


