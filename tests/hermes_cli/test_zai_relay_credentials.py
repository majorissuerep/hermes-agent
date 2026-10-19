"""A configured ZAI relay must be selected before credential endpoint detection."""
from unittest.mock import Mock

import pytest

from hermes_cli import auth, auth_zai_kimi


@pytest.mark.parametrize("configured_provider,env_url,expected,probes", [
    ("zai", "", "https://relay.example/zai/v1", 0),
    ("anthropic", "", "https://api.z.ai/api/paas/v4", 1),
    ("zai", "https://explicit.example/v4", "https://explicit.example/v4", 0),
])
def test_zai_credentials_do_not_probe_vendors_for_an_owned_relay(
    monkeypatch, configured_provider, env_url, expected, probes,
):
    monkeypatch.setattr(auth, "read_raw_config", lambda: {
        "model": {"provider": configured_provider, "base_url": "https://relay.example/zai/v1"}})
    monkeypatch.setattr(auth, "_resolve_api_key_provider_secret", lambda *args: ("synthetic-relay-key", "env"))
    monkeypatch.setenv("GLM_BASE_URL", env_url)
    monkeypatch.setattr(auth, "_load_auth_store", dict)
    monkeypatch.setattr(auth_zai_kimi, "_zai_probe_failed_until", {})
    probe = Mock(return_value=None)
    monkeypatch.setattr(auth, "detect_zai_endpoint", probe)
    result = auth.resolve_api_key_provider_credentials("zai")
    assert result["api_key"] == "synthetic-relay-key"
    assert result["base_url"] == expected
    assert probe.call_count == probes
