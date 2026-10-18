"""Outgoing MCP enablement uses the native profile config, not a retired HTTP host."""

import pytest


def test_enable_persists_only_the_requested_server_in_the_owning_profile(tmp_path, monkeypatch):
    from hermes_cli import config
    from tools.connectors.mcp import _CatalogBackend

    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    config.save_config({"mcp_servers": {
        "chosen": {"command": "chosen-server", "enabled": False},
        "other": {"command": "other-server", "enabled": False},
    }, "model": "test-model"})
    _CatalogBackend().enable("chosen")
    saved = config.load_config()
    assert saved["mcp_servers"]["chosen"] == {"command": "chosen-server", "enabled": True}
    assert saved["mcp_servers"]["other"] == {"command": "other-server", "enabled": False}
    assert saved["model"] == "test-model"
    before = config.get_config_path().read_bytes()
    with pytest.raises(ValueError, match="not a configured MCP server"):
        _CatalogBackend().enable("missing")
    assert config.get_config_path().read_bytes() == before
