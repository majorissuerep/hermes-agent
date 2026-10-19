"""Changing the destination never edits the established conversation prefix."""

from unittest.mock import patch

from tests.agent.test_switch_model_context import _make_agent_with_compressor


def test_explicit_model_round_trip_keeps_frozen_prompt_and_tools():
    agent = _make_agent_with_compressor()
    prompt = "Frozen instructions\nModel: primary-model\nProvider: openrouter\n"
    tools = [{"type": "function", "function": {"name": "terminal"}}]
    agent._cached_system_prompt = prompt
    agent._cached_system_prompt_static = "Frozen instructions\n"
    agent.tools = tools
    with (
        patch("agent.model_metadata.get_model_context_length", return_value=131072),
        patch("hermes_cli.config.load_config", return_value={}),
        patch("hermes_cli.config.load_config_readonly", return_value={}),
    ):
        for destination in ("second-model", "primary-model"):
            agent.switch_model(destination, "openrouter", api_key="synthetic", base_url="https://openrouter.ai/api/v1")
            assert agent.model == destination
            assert agent._primary_runtime["model"] == destination
            assert agent._cached_system_prompt == prompt
            assert agent._cached_system_prompt_static == "Frozen instructions\n"
            assert agent.tools is tools
