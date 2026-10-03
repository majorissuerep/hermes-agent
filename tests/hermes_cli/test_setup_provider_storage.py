"""Setup writes the provider map consumed by every settings surface."""
import pytest

from hermes_security import vault


@pytest.mark.parametrize("legacy", [False, True])
def test_setup_creates_updates_and_removes_canonical_provider(tmp_path, monkeypatch, legacy):
    from hermes_cli.config import read_raw_config, save_config
    from hermes_cli.config_providers import get_compatible_custom_providers
    from hermes_cli.main_provider_setup import _save_custom_provider, _remove_custom_provider

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault.init_vault(home, "setup-provider-password")
    vault.unlock(home, "setup-provider-password")
    try:
        if legacy:
            save_config({"custom_providers": [{"name": "Local server", "base_url": "http://127.0.0.1:8080/v1",
                                               "extra_headers": {"X-Custom": "preserved"}}]})
        _save_custom_provider("http://127.0.0.1:8080/v1", model="first", name="Local server",
                              key_env="HERMES_CUSTOM_LOCAL_API_KEY", api_mode="chat_completions")
        config = read_raw_config()
        first = get_compatible_custom_providers(config)[0]
        assert first["provider_key"] in config["providers"]
        if legacy:
            assert first["extra_headers"] == {"X-Custom": "preserved"}
            assert not config.get("custom_providers")
        _save_custom_provider("http://127.0.0.1:8080/v1/", model="second", context_length=12345,
                              key_env="HERMES_CUSTOM_LOCAL_API_KEY", api_mode="chat_completions")
        config = read_raw_config()
        entries = get_compatible_custom_providers(config)
        assert [entry["provider_key"] for entry in entries] == [first["provider_key"]]
        assert entries[0]["model"] == "second" and entries[0]["models"]["second"]["context_length"] == 12345
        assert entries[0]["key_env"] == "HERMES_CUSTOM_LOCAL_API_KEY"
        monkeypatch.setattr("hermes_cli.main_provider_setup._radiolist", lambda *args, **kwargs: 0)
        _remove_custom_provider(config)
        assert get_compatible_custom_providers(read_raw_config()) == []
        assert (home / "config.yaml").read_bytes().startswith(b"HRMVAULT\x00")
    finally:
        vault.clear_vault_cache()
