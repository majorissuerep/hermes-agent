"""Readiness sees scoped encrypted inference credentials, never media-only keys."""
import base64
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("credential", ["api", "oauth"])
def test_encrypted_readiness_and_setup_follow_supported_profiles_a_b_a(tmp_path, monkeypatch, credential):
    from agent.secret_scope import (build_profile_secret_scope, set_secret_scope, reset_secret_scope,
                                    set_multiplex_context, reset_multiplex_context)
    from hermes_cli.auth import _save_auth_store
    from hermes_cli.auth_codex import _save_codex_tokens
    from hermes_cli.config import save_config, save_env_value
    from hermes_cli.main import _has_any_provider_configured
    from hermes_cli.main_provider_state import _dotenv_has_provider_key
    from hermes_cli.setup_migration import _model_section_has_credentials
    from hermes_security import vault

    homes = [tmp_path / name for name in ("a", "b")]
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    try:
        for home in homes:
            monkeypatch.setenv("HERMES_HOME", str(home))
            vault.init_vault(home, "readiness-fixture-password")
            vault.unlock(home, "readiness-fixture-password")
            save_config({"model": ""}, merge_existing=False)
            if home == homes[0]:
                save_env_value("OPENROUTER_API_KEY", "synthetic-media-only")
                _save_auth_store({"version": 1, "active_provider": "nous", "providers": {}})
            elif credential == "api":
                save_env_value("OPENAI_API_KEY", "synthetic-inference-key")
            else:
                def segment(value):
                    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
                token = segment({"alg": "none"}) + "." + segment({"exp": 4102444800}) + ".fixture"
                _save_codex_tokens({"access_token": token, "refresh_token": "synthetic-no-refresh",
                                    "account_id": "fixture-account"}, set_active=True)
        for home in (homes[0], homes[1], homes[0]):
            monkeypatch.setenv("HERMES_HOME", str(home))
            scope = set_secret_scope(build_profile_secret_scope(home), profile_home=str(home))
            multiplex = set_multiplex_context(True)
            try:
                expected = home == homes[1]
                actual = (_has_any_provider_configured(strict_profile_scope=True),
                          _model_section_has_credentials({"model": ""}))
                assert actual == (expected, expected)
                assert _dotenv_has_provider_key(home / ".env", {"OPENAI_API_KEY"}) is (expected and credential == "api")
            finally:
                reset_multiplex_context(multiplex)
                reset_secret_scope(scope)
    finally:
        vault.clear_vault_cache()
