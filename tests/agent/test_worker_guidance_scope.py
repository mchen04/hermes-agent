from types import SimpleNamespace
from agent.agent_init_tools import _load_tools
from agent.delegation_context import non_dispatcher_owned_context
from agent.prompt_builder import KANBAN_GUIDANCE


def test_worker_guidance_requires_worker_identity(monkeypatch):
    import model_tools
    import hermes_cli.plugins
    monkeypatch.setattr(hermes_cli.plugins, 'discover_plugins', lambda: None)
    monkeypatch.setattr(model_tools, 'get_tool_definitions', lambda **kw: [{'function': {'name': 'kanban_show'}}])
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    agent = SimpleNamespace(quiet_mode=True)
    _load_tools(agent, [], [])
    assert not agent._kanban_worker_guidance
    monkeypatch.setenv('HERMES_KANBAN_TASK', 'synthetic-worker')
    _load_tools(agent, [], [])
    assert agent._kanban_worker_guidance == KANBAN_GUIDANCE
    with non_dispatcher_owned_context():
        child = SimpleNamespace(quiet_mode=True)
        _load_tools(child, [], [])
        assert not child._kanban_worker_guidance
    assert agent._kanban_worker_guidance == KANBAN_GUIDANCE


def test_composed_front_door_prompt_never_infers_worker_from_tools(tmp_path, monkeypatch):
    from agent.system_prompt import build_system_prompt_parts
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('TERMINAL_CWD', str(tmp_path))
    for profile in ['default', 'forge', 'argus']:
        agent = SimpleNamespace(load_soul_identity=False, skip_context_files=True,
            valid_tool_names={'kanban_show'}, _tool_use_enforcement=False,
            _environment_probe=False, _memory_store=None, _memory_manager=None,
            model='fixture', provider='fixture', platform='discord', pass_session_id=False,
            session_id='', _plugin_system_prompt_sections_snapshot=(),
            _hermes_home_override=str(tmp_path / profile))
        assert KANBAN_GUIDANCE not in build_system_prompt_parts(agent)['stable']
        agent._kanban_worker_guidance = KANBAN_GUIDANCE
        assert KANBAN_GUIDANCE in build_system_prompt_parts(agent)['stable']
