"""A selected external-process provider must survive child command resolution."""
import sys
from types import SimpleNamespace

import pytest

from providers import register_provider
from providers.base import ProviderProfile
from tools.delegate_tool_config import _resolve_child_runtime


def test_pinned_command_retains_selected_external_provider():
    profile = ProviderProfile(name="test-process-provider", display_name="Test", auth_type="external_process")
    register_provider(profile)
    parent = SimpleNamespace(model="test-model", provider=profile.name, base_url="process://test", api_mode="chat_completions", acp_args=[])
    result = _resolve_child_runtime(
        parent, {}, "external-process", model=None, override_provider=profile.name,
        override_base_url=None, override_api_key=None, override_api_mode=None,
        override_acp_command=sys.executable, override_acp_args=[],
    )
    assert result["provider"] == profile.name
    assert result["requested_provider"] == result["provider"]
    assert result["acp_command"] == sys.executable


@pytest.mark.parametrize("provider", ["test-native-provider", "test-unregistered-provider"])
def test_process_command_cannot_turn_native_provider_into_another_protocol(provider):
    register_provider(ProviderProfile(name="test-native-provider", display_name="Native", auth_type="api_key"))
    parent = SimpleNamespace(model="test-model", provider=provider, base_url="https://unused.invalid", api_mode="chat_completions")
    with pytest.raises(ValueError, match="requires an external_process provider"):
        _resolve_child_runtime(
            parent, {}, "synthetic-key", model=None, override_provider=provider,
            override_base_url=None, override_api_key=None, override_api_mode=None,
            override_acp_command=sys.executable, override_acp_args=[],
        )
