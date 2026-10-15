"""Bundled discovery, setup and runtime share one intentionally bounded surface."""
import pytest


def test_removed_bundled_providers_cannot_return_through_metadata_or_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    from providers import list_providers
    from hermes_cli.auth import AuthError, resolve_provider
    from hermes_cli.providers import get_provider
    from hermes_cli.models_catalog_static import listed_canonical_providers
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from hermes_cli.auth_commands import auth_add_command
    from hermes_cli.models_detect import provider_has_credentials
    from types import SimpleNamespace

    supported = {"openai-codex", "openai-api", "anthropic", "litellm", "custom",
                 "zai", "xai", "xai-oauth", "ai-gateway"}
    assert {p.name for p in list_providers()} <= supported
    assert {p.slug for p in listed_canonical_providers()} == supported
    for name in ("openrouter", "nous", "gemini", "bedrock", "fireworks", "deepseek", "copilot"):
        assert get_provider(name, allow_network=False) is None
        with pytest.raises(AuthError):
            resolve_provider(name)
        with pytest.raises(AuthError):
            resolve_runtime_provider(requested=name)
        with pytest.raises(SystemExit, match="Unknown provider"):
            auth_add_command(SimpleNamespace(provider=name, auth_type="api_key",
                                              api_key="synthetic-unused-provider-key"))
    assert resolve_provider("chatgpt") == "openai-codex"
    assert resolve_provider("grok") == "xai"
    assert resolve_provider("vercel") == "ai-gateway"
    assert resolve_provider("openai") == "openai-api"
    monkeypatch.setenv("OPENROUTER_API_KEY", "media-tool-only-fixture")
    assert not provider_has_credentials("openrouter")
    from hermes_cli import anon_auth, welcome_trial
    assert not anon_auth.welcome_provider_available()
    assert not anon_auth.free_inference_active()
    assert welcome_trial.claim_blocker(assume_fresh=True) is not None


def test_encrypted_gateway_and_custom_protocol_config_survive_a_b_a(tmp_path, monkeypatch):
    from hermes_security import vault
    from hermes_cli.config import save_config, save_env_value
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from hermes_cli.providers import resolve_provider_full
    from agent.secret_scope import build_profile_secret_scope, set_secret_scope, reset_secret_scope, set_multiplex_context, reset_multiplex_context

    homes = [tmp_path / name for name in ("A", "B")]
    try:
        for home in homes:
            monkeypatch.setenv("HERMES_HOME", str(home))
            vault.init_vault(home, "synthetic-provider-password")
            vault.unlock(home, "synthetic-provider-password")
            save_env_value("PROVIDER_TEST_API_KEY", "synthetic-key-" + home.name)
            save_config({
                "model": {"provider": "litellm", "default": "test-model"},
                "providers": {
                    "litellm": {"base_url": f"https://{home.name.lower()}.invalid/v1", "key_env": "PROVIDER_TEST_API_KEY"},
                    "custom-claude": {"base_url": f"https://{home.name.lower()}.invalid/anthropic",
                                      "key_env": "PROVIDER_TEST_API_KEY", "api_mode": "anthropic_messages"},
                },
            }, merge_existing=True)
        for home in (homes[0], homes[1], homes[0]):
            monkeypatch.setenv("HERMES_HOME", str(home))
            scope = set_secret_scope(build_profile_secret_scope(home), profile_home=str(home))
            multiplex = set_multiplex_context(True)
            try:
                gateway = resolve_runtime_provider(requested="litellm")
                assert gateway["base_url"] == f"https://{home.name.lower()}.invalid/v1"
                assert gateway["api_key"] == "synthetic-key-" + home.name
                custom = resolve_runtime_provider(requested="custom-claude")
                assert custom["api_mode"] == "anthropic_messages"
                assert custom["api_key"] == gateway["api_key"]
                explicit = resolve_provider_full("my-openai", user_providers={"my-openai": {"base_url": gateway["base_url"]}})
                assert explicit.base_url == gateway["base_url"]
            finally:
                reset_multiplex_context(multiplex)
                reset_secret_scope(scope)
    finally:
        vault.clear_vault_cache()


def test_auto_uses_each_homes_manual_pool_but_never_retired_credentials(tmp_path, monkeypatch):
    from hermes_security import vault
    from hermes_cli.auth import AuthError, resolve_provider, write_credential_pool
    from hermes_cli.config import save_config
    from agent.secret_scope import (build_profile_secret_scope, set_secret_scope, reset_secret_scope,
                                    set_multiplex_context, reset_multiplex_context)

    homes = [tmp_path / name for name in ("A", "B")]
    try:
        for home in homes:
            monkeypatch.setenv("HERMES_HOME", str(home))
            vault.init_vault(home, "synthetic-auto-pool-password")
            vault.unlock(home, "synthetic-auto-pool-password")
            save_config({"model": {"provider": "auto"}}, merge_existing=True)
            for provider in ("openrouter", "ai-gateway"):
                write_credential_pool(provider, [{"id": home.name, "source": "manual", "auth_type": "api_key",
                    "access_token": "synthetic-" + home.name, "base_url": "https://ai-gateway.vercel.sh/v1"}])
        for home in (homes[0], homes[1], homes[0]):
            monkeypatch.setenv("HERMES_HOME", str(home))
            scope = set_secret_scope(build_profile_secret_scope(home), profile_home=str(home))
            multiplex = set_multiplex_context(True)
            try:
                assert resolve_provider("auto") == "ai-gateway"
                write_credential_pool("ai-gateway", [], removed_ids={home.name})
                with pytest.raises(AuthError, match="not connected"):
                    resolve_provider("auto")
                write_credential_pool("ai-gateway", [{"id": home.name, "source": "manual", "auth_type": "api_key",
                    "access_token": "synthetic-" + home.name, "base_url": "https://ai-gateway.vercel.sh/v1"}])
                assert (home / "auth.json").read_bytes().startswith(b"HRMVAULT")
            finally:
                reset_multiplex_context(multiplex)
                reset_secret_scope(scope)
    finally:
        vault.clear_vault_cache()
