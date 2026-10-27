import re

from typing import Any


def bound_skill_rows(messages: list[dict[str, Any]]) -> list[tuple[int, list[str]]]:
    from agent.skill_commands import _AUTO_LOAD_PREFIX, skills_loaded_by_scaffold

    return [
        (index, names)
        for index, message in enumerate(messages)
        if message.get("role") == "user"
        and isinstance(content := message.get("content"), str)
        and content.startswith(_AUTO_LOAD_PREFIX)
        and (names := skills_loaded_by_scaffold(content))
    ]


def bound_skill_names(messages: list[dict[str, Any]]) -> set[str]:
    return {_skill_key(name) for _, names in bound_skill_rows(messages) for name in names}


def bound_skill_head_end(messages: list[dict[str, Any]]) -> int:
    return max((index + 1 for index, _ in bound_skill_rows(messages)), default=0)

_LIVE_SKILL_BANNER_RE = re.compile(
    r'\[IMPORTANT: The (?:user launched this CLI session with the )?"([^"]+)" skill (?:is auto-loaded|preloaded)'
)


def _skill_key(name: str) -> str:
    return (name or "").strip().lower().rsplit("/", 1)[-1]


def _collect_live_skill_names(kept: list[dict[str, Any]], pinned_text: str = "") -> set[str]:
    """Skills whose SKILL.md text survives this compaction: a full ``skill_view`` body (no file_path) in the
    kept head/tail, or a pinned/auto-loaded banner there or in the system prompt. A prune marker for one of
    these sends the model to reload text it can still see (and feeds the identical-result loop guard)."""
    from agent.context_compressor import (SKILL_PRUNED_MARKER_PREFIX, _PRUNE_MIN_CHARS, _content_text_for_contains, _is_summary_stub, _json_dict, _tc_get)

    live = {_skill_key(m.group(1)) for m in _LIVE_SKILL_BANNER_RE.finditer(pinned_text or "")}
    body_calls: dict[str, str] = {}
    for msg in kept:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                fn = _tc_get(tc, "function", {})
                if _tc_get(fn, "name") != "skill_view":
                    continue
                args = _json_dict(_tc_get(fn, "arguments") or "{}")
                if (args.get("file_path") or "SKILL.md") == "SKILL.md" and isinstance(args.get("name"), str):
                    body_calls[str(_tc_get(tc, "id"))] = args["name"]
        content = _content_text_for_contains(msg.get("content"))
        live.update(_skill_key(m.group(1)) for m in _LIVE_SKILL_BANNER_RE.finditer(content))
        name = body_calls.get(str(msg.get("tool_call_id") or ""))
        if (msg.get("role") == "tool" and name and len(content) > _PRUNE_MIN_CHARS
                and SKILL_PRUNED_MARKER_PREFIX not in content and not _is_summary_stub(content)):
            live.add(_skill_key(name))
    return live


def _strip_pruned_skill_markers(summary: str) -> str:
    """Drop marker lines and the Pruned Skills block the summarizer copied; the caller re-injects the current set.
    Copied markers ratchet: every iterative update carried every earlier marker forward, even after a reload."""
    from agent.context_compressor import (SKILL_PRUNED_MARKER_PREFIX, _PRUNED_SKILLS_SECTION_HEADING)

    summary = re.sub(
        r"\n*" + re.escape(_PRUNED_SKILLS_SECTION_HEADING) + r"\n.*?(?=\n## |\Z)", "\n", summary or "", flags=re.DOTALL,
    )
    return re.sub(r"[ \t]*" + re.escape(SKILL_PRUNED_MARKER_PREFIX) + r"[^\]\n]*\]", "", summary).rstrip()

def _skill_pruned_marker(skill_name: str) -> str:
    """Return the canonical prune marker for *skill_name* (shared by emit and check sites)."""
    from agent.context_compressor import (SKILL_PRUNED_MARKER_PREFIX)

    return (
        f"{SKILL_PRUNED_MARKER_PREFIX} content lost in compression; "
        f"reload with skill_view(name='{skill_name}')]"
    )


def _extract_pruned_skill_names(text: str) -> list[str]:
    """Return skill names referenced by prune markers in *text*, in order."""
    from agent.context_compressor import (_SKILL_PRUNED_MARKER_RE)

    return list(dict.fromkeys(m.group(1) for m in _SKILL_PRUNED_MARKER_RE.finditer(text or "")))


def _collect_ghosted_skill_names(turns: list[dict[str, Any]]) -> list[str]:
    """Skills about to be lost in compaction, whatever their size: marked or plain ``skill_view`` stubs, raw
    ``skill_view`` bodies, and skills a ``/skill``, bundle or auto-load turn carried inline."""
    from agent.context_compressor import (_content_text_for_contains, _looks_like_compaction_summary, _skill_view_failed, is_demoted_stub, skill_view_names_by_call_id)

    from agent.skill_commands import skills_loaded_by_scaffold
    call_id_to_skill = skill_view_names_by_call_id(turns)
    names: list[str] = []
    for msg in turns:
        content = msg.get("content")
        text = _content_text_for_contains(content)
        # LOCAL-PATCH skill-reload-markers: an earlier handoff's markers are history, not a loss at this
        # boundary; carrying them forward made the model reload the same skills on every later turn.
        if not _looks_like_compaction_summary(msg, text):
            names += _extract_pruned_skill_names(text)
        if msg.get("role") == "user":
            names += skills_loaded_by_scaffold(text)
        elif msg.get("role") == "tool" and not _skill_view_failed(content) and not is_demoted_stub(text):
            names.append(call_id_to_skill.get(str(msg.get("tool_call_id") or ""), ""))
    return [name for name in dict.fromkeys(names) if name]
