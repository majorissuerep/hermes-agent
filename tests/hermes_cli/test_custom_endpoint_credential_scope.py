"""A profile's endpoint override must not move a config-bound credential."""


def test_encrypted_custom_overrides_keep_keys_at_their_declared_endpoint_a_b_a(tmp_path, monkeypatch):
    from agent.secret_scope import (build_profile_secret_scope, set_secret_scope, reset_secret_scope,
                                    set_multiplex_context, reset_multiplex_context)
    from hermes_cli.config import save_config, save_env_value
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from hermes_security import vault

    homes = [tmp_path / name for name in ("A", "B")]
    try:
        for home in homes:
            monkeypatch.setenv("HERMES_HOME", str(home))
            vault.init_vault(home, "synthetic-custom-scope-password")
            vault.unlock(home, "synthetic-custom-scope-password")
            save_config({"model": {"provider": "custom", "default": "test-model",
                "base_url": f"https://configured.{home.name.lower()}.invalid/v1",
                "key_env": "TEST_ENDPOINT_API_KEY"}}, merge_existing=True)
            save_env_value("TEST_ENDPOINT_API_KEY", "synthetic-key-" + home.name)
            save_env_value("CUSTOM_BASE_URL", f"https://override.{home.name.lower()}.invalid/v1")
        for home in (homes[0], homes[1], homes[0]):
            monkeypatch.setenv("HERMES_HOME", str(home))
            scope = set_secret_scope(build_profile_secret_scope(home), profile_home=str(home))
            multiplex = set_multiplex_context(True)
            try:
                runtime = resolve_runtime_provider(requested="custom")
                assert (runtime["base_url"], runtime["api_key"]) == (
                    f"https://override.{home.name.lower()}.invalid/v1", "no-key-required")
                configured = resolve_runtime_provider(requested="custom",
                    explicit_base_url=f"https://configured.{home.name.lower()}.invalid/v1")
                assert configured["api_key"] == "synthetic-key-" + home.name
                assert (home / ".env").read_bytes().startswith(b"HRMVAULT")
            finally:
                reset_multiplex_context(multiplex)
                reset_secret_scope(scope)
    finally:
        vault.clear_vault_cache()
