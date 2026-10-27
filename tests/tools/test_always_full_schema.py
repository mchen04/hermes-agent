from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from tools import tool_search as ts


def _schema(name):
    return {"type": "function", "function": {
        "name": name, "description": "Use the complete tool contract.",
        "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
    }}


def test_named_schema_pin_overrides_deferral_and_stays_profile_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "_registry_toolset", lambda name: "kanban")
    incoming = [_schema("kanban_schedule"), _schema("kanban_show")]
    for profile, pinned in (("pinned", True), ("ordinary", False)):
        home = tmp_path / profile
        home.mkdir()
        (home / "config.yaml").write_text(
            "tools:\n  always_full_schema: " + ("[kanban_schedule]" if pinned else "[]") +
            "\n  tool_search:\n    enabled: on\n    defer: [kanban_schedule, kanban_show]\n",
        )
        token = set_hermes_home_override(home)
        try:
            config = ts.load_config_readonly()
            result = ts.assemble_tool_defs(incoming, context_length=100_000, config=config)
            direct = {tool["function"]["name"]: tool for tool in result.tool_defs}
            assert ("kanban_schedule" in direct) is pinned
            if pinned:
                assert direct["kanban_schedule"] == incoming[0]
            assert "kanban_show" not in direct
            assert ts.scoped_deferrable_names(incoming) == (
                frozenset({"kanban_show"}) if pinned else frozenset({"kanban_schedule", "kanban_show"})
            )
            assert "kanban_schedule" not in {
                tool["function"]["name"] for tool in ts.assemble_tool_defs([], context_length=100_000, config=config).tool_defs
            }
        finally:
            reset_hermes_home_override(token)
