"""Tests for is_provider_explicitly_configured()."""

import json
import pytest


def _write_config(tmp_path, config: dict) -> None:
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir(parents=True, exist_ok=True)
    import hermes_yaml as yaml
    (hermes_home / "config.yaml").write_text(yaml.safe_dump(config))


def _write_auth_store(tmp_path, payload: dict) -> None:
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir(parents=True, exist_ok=True)
    (hermes_home / "auth.json").write_text(json.dumps(payload, indent=2))


@pytest.fixture(autouse=True)
def _clean_anthropic_env(monkeypatch):
    """Strip Anthropic env vars so CI secrets don't leak into tests."""
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(key, raising=False)


def test_ambient_pool_source_does_not_count_as_explicit(tmp_path, monkeypatch):
    """gh_cli-seeded Copilot pool entries are ambient, not explicit config (#56974)."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.delenv("COPILOT_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    _write_auth_store(tmp_path, {
        "version": 1,
        "providers": {},
        "active_provider": None,
        "credential_pool": {
            "copilot": [{
                "id": "abc123",
                "source": "gh_cli",
                "auth_type": "api_key",
                "access_token": "ghu_sometoken",
            }],
        },
    })

    from hermes_cli.auth import is_provider_explicitly_configured
    assert is_provider_explicitly_configured("copilot") is False


def test_returns_true_when_moa_reference_slot_uses_provider(tmp_path, monkeypatch):
    """MoA advisor slots are explicit provider selections for auth gating."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    _write_config(tmp_path, {
        "model": {"provider": "openai-codex", "default": "gpt-5.5"},
        "moa": {
            "presets": {
                "default": {
                    "reference_models": [
                        {"provider": "anthropic", "model": "claude-opus-4-8"},
                        {"provider": "opencode-go", "model": "glm-5.2"},
                    ],
                    "aggregator": {"provider": "openai-codex", "model": "gpt-5.5"},
                }
            }
        },
    })
    _write_auth_store(tmp_path, {"version": 1, "providers": {}, "active_provider": "openai-codex"})

    from hermes_cli.auth import is_provider_explicitly_configured
    assert is_provider_explicitly_configured("anthropic") is True


def test_stale_env_pool_entry_does_not_count_when_var_unset(tmp_path, monkeypatch):
    """An env-seeded pool entry left in auth.json after the env var was removed
    must not mark the provider configured (#55790): the picker showed removed
    providers forever because the record existed even though no secret resolves."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    _write_auth_store(tmp_path, {
        "version": 1,
        "providers": {},
        "active_provider": None,
        "credential_pool": {
            "zai": [{
                "id": "aaa111",
                "source": "env:GLM_API_KEY",
                "auth_type": "api_key",
            }],
        },
    })

    from hermes_cli.auth import is_provider_explicitly_configured
    assert is_provider_explicitly_configured("zai") is False


def test_profile_dotenv_key_counts_as_explicit_when_process_env_lacks_it(tmp_path, monkeypatch):
    """A Z.AI key in a named profile's .env must count under that profile's
    secret scope even when os.environ (the launch profile) has no Z.AI var.

    Desktop Settings → Model uses explicit_only, which filters through
    is_provider_explicitly_configured. os.getenv hid the keyed provider until
    Refresh models ran against the bot's own backend.
    """
    root = tmp_path / "hermes"
    studio = root / "profiles" / "content-studio"
    studio.mkdir(parents=True)
    (root / "config.yaml").write_text("model: {}\n")
    (studio / "config.yaml").write_text("model: {}\n")
    (studio / ".env").write_text("GLM_API_KEY=sk-studio-zai-key\n")
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.delenv("GLM_API_KEY", raising=False)

    from agent import secret_scope
    from tui_runtime import launch_profile_policy as lpp
    monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", False)
    monkeypatch.setattr(lpp, "_snapshot", None)

    from hermes_cli.auth import is_provider_explicitly_configured
    from hermes_cli.web_server_profiles import _config_profile_scope

    assert is_provider_explicitly_configured("zai") is False
    with _config_profile_scope("content-studio"):
        assert is_provider_explicitly_configured("zai") is True
    with _config_profile_scope(None):
        assert is_provider_explicitly_configured("zai") is False


def test_dotenv_key_counts_when_shell_exports_the_var_empty(tmp_path, monkeypatch):
    """The gate resolves through the same reader as the credential resolver
    (get_env_value_prefer_dotenv), so it agrees with the key that authenticates:
    an empty ``GLM_API_KEY=`` inherited from the parent shell must not hide
    a real key in .env (#77007) — the resolver would use that key, so the picker
    must list the provider."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("GLM_API_KEY", "")
    _write_config(tmp_path, {"model": {}})
    (tmp_path / "hermes" / ".env").write_text("GLM_API_KEY=sk-dotenv-only-secret\n")

    from hermes_cli.auth import is_provider_explicitly_configured, resolve_api_key_provider_credentials
    assert resolve_api_key_provider_credentials("zai").get("api_key") == "sk-dotenv-only-secret"
    assert is_provider_explicitly_configured("zai") is True


# ─── aws_sdk providers (Bedrock) ─────────────────────────────────────────
#
# Bedrock is registered with auth_type="aws_sdk" and an empty
# api_key_env_vars tuple, so the api_key env-var loop never sees it.
# Setting AWS_BEARER_TOKEN_BEDROCK (or an access-key pair) in .env is
# exactly as explicit as pasting ANTHROPIC_API_KEY, and must count —
# otherwise the desktop picker's explicit_only filter hides Bedrock even
# though list_authenticated_providers builds a full row for it.
#
# Ambient AWS credential sources (SSO profiles, EC2 IMDS, container
# credentials) must NOT count: aws_sdk detection here is env-var only,
# never boto3's full chain.


@pytest.fixture()
def _clean_aws_env(monkeypatch):
    """Strip AWS env vars so developer/CI AWS credentials don't leak in."""
    for key in (
        "AWS_BEARER_TOKEN_BEDROCK",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
    ):
        monkeypatch.delenv(key, raising=False)


def test_aws_env_does_not_leak_into_other_providers(tmp_path, monkeypatch, _clean_aws_env):
    """The aws_sdk env check must key on the provider's auth_type, not fire
    for every provider whenever AWS credentials happen to be present."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    (tmp_path / "hermes").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "ABSKexample-bearer-token-value")

    from hermes_cli.auth import is_provider_explicitly_configured
    assert is_provider_explicitly_configured("anthropic") is False
