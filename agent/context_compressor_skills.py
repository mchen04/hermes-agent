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
    return {name.strip().lower() for _, names in bound_skill_rows(messages) for name in names}


def bound_skill_head_end(messages: list[dict[str, Any]]) -> int:
    return max((index + 1 for index, _ in bound_skill_rows(messages)), default=0)
