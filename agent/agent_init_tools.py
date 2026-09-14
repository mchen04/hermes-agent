"""Session-static tool snapshot and tool guidance during agent construction."""
import logging
import os

logger = logging.getLogger("run_agent")


def _load_tools(agent, enabled_toolsets, disabled_toolsets):
    # A multiplexed gateway may have switched HERMES_HOME since model_tools was imported;
    # make sure this profile's plugins are discovered before the tool snapshot.
    try:
        from hermes_cli.plugins import discover_plugins
        discover_plugins()
    except Exception:
        logger.warning("Plugin discovery failed during agent setup", exc_info=True)

    # Capture the registry generation FIRST so a concurrent refresh can detect staleness.
    try:
        from tools.registry import registry as _snapshot_registry
        agent._tool_snapshot_generation = _snapshot_registry._generation
    except Exception:
        agent._tool_snapshot_generation = 0
    import model_tools
    agent.tools = model_tools.get_tool_definitions(
        enabled_toolsets=enabled_toolsets, disabled_toolsets=disabled_toolsets,
        quiet_mode=agent.quiet_mode,
    )

    agent.valid_tool_names = {tool["function"]["name"] for tool in agent.tools} if agent.tools else set()
    # Kanban guidance is session-static; only a dispatcher-owned worker identity grants it.
    from agent.prompt_builder import KANBAN_GUIDANCE
    from agent.delegation_context import is_dispatcher_owned_worker_context
    agent._kanban_worker_guidance = (
        KANBAN_GUIDANCE
        if (os.environ.get("HERMES_KANBAN_TASK")
            and is_dispatcher_owned_worker_context()
            and "kanban_show" in agent.valid_tool_names)
        else ""
    )
    if agent.quiet_mode:
        return
    if agent.tools:
        print(f"🛠️  Loaded {len(agent.tools)} tools: {', '.join(sorted(agent.valid_tool_names))}")
        if enabled_toolsets:
            print(f"   ✅ Enabled toolsets: {', '.join(enabled_toolsets)}")
        if disabled_toolsets:
            print(f"   ❌ Disabled toolsets: {', '.join(disabled_toolsets)}")
        requirements = model_tools.check_toolset_requirements()
        missing_reqs = [name for name, available in requirements.items() if not available]
        if missing_reqs:
            print(f"⚠️  Some tools may not work due to missing requirements: {missing_reqs}")
    else:
        print("🛠️  No tools loaded (all tools filtered out or unavailable)")
    if agent.save_trajectories:
        print("📝 Trajectory saving enabled")
    if agent.ephemeral_system_prompt:
        prompt_preview = agent.ephemeral_system_prompt[:60] + "..." if len(agent.ephemeral_system_prompt) > 60 else agent.ephemeral_system_prompt
        print(f"🔒 Ephemeral system prompt: '{prompt_preview}' (not saved to trajectories)")
    if agent._use_prompt_caching:
        if agent._use_native_cache_layout and agent.provider == "anthropic":
            source = "native Anthropic"
        elif agent._use_native_cache_layout:
            source = "Anthropic-compatible endpoint"
        else:
            source = "Claude via OpenRouter"
        print(f"💾 Prompt caching: ENABLED ({source}, {agent._cache_ttl} TTL)")
