"""Tool discovery reads the owning encrypted credential store without a network call."""
import json

import pytest


@pytest.mark.parametrize("source", ["singleton", "pool"])
def test_encrypted_xai_login_is_available_only_in_its_own_profile(tmp_path, monkeypatch, source):
    from agent.secret_scope import reset_secret_scope, set_secret_scope
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_security import io, vault
    from tools.xai_http import has_xai_credentials

    homes = [tmp_path / name for name in ("owner", "other")]
    token_value = "synthetic-xai-login"
    store = ({"providers": {"xai-oauth": {"tokens": {"access_token": token_value}}}}
             if source == "singleton" else {"credential_pool": {"xai-oauth": [{"access_token": token_value}]}})
    monkeypatch.setenv("HERMES_HOME", str(homes[0]))
    scope = set_secret_scope({})
    try:
        for home, data in zip(homes, (store, {})):
            vault.init_vault(home, "synthetic-availability-password")
            vault.unlock(home, "synthetic-availability-password")
            io.write_text(home / "auth.json", json.dumps(data), purpose="auth")
        original = (homes[0] / "auth.json").read_bytes()
        assert has_xai_credentials() is True
        selected = set_hermes_home_override(homes[1])
        try:
            assert has_xai_credentials() is False
        finally:
            reset_hermes_home_override(selected)
        assert (homes[0] / "auth.json").read_bytes() == original
        assert token_value.encode() not in original
        vault.lock_now(homes[0])
        assert has_xai_credentials() is False
    finally:
        reset_secret_scope(scope)
        vault.clear_vault_cache()
