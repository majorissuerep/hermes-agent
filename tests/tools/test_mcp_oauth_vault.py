"""Outgoing MCP credentials obey the owning profile's encrypted state boundary."""

import secrets

import pytest


def test_oauth_state_is_ciphertext_and_requires_the_owner_key(tmp_path, monkeypatch):
    from hermes_security import vault
    from hermes_security.errors import VaultLockedError
    from tools import mcp_oauth

    home = tmp_path / "profile"
    password = secrets.token_urlsafe(24)
    vault.init_vault(home, password)
    vault.unlock(home, password)
    monkeypatch.setenv("HERMES_HOME", str(home))
    path = home / "mcp-tokens" / "native.json"
    token = secrets.token_urlsafe(32)
    payload = {"access_token": token, "expires_at": 12345}
    mcp_oauth._write_json(path, payload)
    raw = path.read_bytes()
    assert raw.startswith(b"HRMVAULT\x00")
    assert token.encode() not in raw
    assert mcp_oauth._read_json(path) == payload
    storage = mcp_oauth.HermesTokenStorage("native", hermes_home=home)
    snapshot = storage.snapshot()
    storage.remove()
    storage.restore(snapshot)
    assert mcp_oauth._read_json(path) == payload
    client = storage._client_info_path()
    mcp_oauth._write_json(client, {"client_id": "native-client", "client_secret": token})
    assert storage.poison_client_registration()
    backup = client.with_name(client.name + ".bak")
    assert token.encode() not in backup.read_bytes()
    assert mcp_oauth._read_json(backup)["client_secret"] == token
    vault.lock_now(home)
    with pytest.raises(VaultLockedError):
        mcp_oauth._read_json(path)
