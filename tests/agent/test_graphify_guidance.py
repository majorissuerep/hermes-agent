"""Graph workflow guidance follows bundled skill and session-tool availability."""

import json


def test_bundled_graphify_guidance_follows_skill_and_tool_availability(tmp_path, monkeypatch):
    from agent.prompt_builder import (
        PROJECT_GRAPH_GUIDANCE, build_skills_system_prompt, clear_skills_system_prompt_cache,
    )
    from hermes_cli.config import save_config
    from tools.skills_sync import sync_skills
    from tools.skills_tool import skill_view

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    tools = {"terminal", "read_file", "write_file", "skill_view"}
    clear_skills_system_prompt_cache(clear_snapshot=True)
    # Real startup seeding, loader, and prompt renderer, without a handcrafted skill fixture.
    sync_skills(quiet=True)
    loaded = json.loads(skill_view("graphify"))
    assert loaded.get("success"), loaded
    assert (tmp_path / "skills" / "software-development" / "graphify" / "scripts" / "project_graph.py").is_file()
    prompt = build_skills_system_prompt(available_tools=tools)
    assert PROJECT_GRAPH_GUIDANCE in prompt
    assert build_skills_system_prompt(available_tools=tools) == prompt
    assert PROJECT_GRAPH_GUIDANCE not in build_skills_system_prompt(available_tools=tools - {"terminal"})
    assert PROJECT_GRAPH_GUIDANCE not in build_skills_system_prompt(available_tools=tools - {"skill_view"})
    monkeypatch.setattr("agent.oneshot_footprint.is_single_query_session", lambda: True)
    clear_skills_system_prompt_cache(clear_snapshot=True)
    assert PROJECT_GRAPH_GUIDANCE in build_skills_system_prompt(available_tools=tools)
    save_config({"skills": {"disabled": ["graphify"]}})
    clear_skills_system_prompt_cache(clear_snapshot=True)
    assert PROJECT_GRAPH_GUIDANCE not in build_skills_system_prompt(available_tools=tools)
