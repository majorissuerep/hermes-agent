"""Existing encrypted OAuth singletons remain usable without weakening vault failures."""

import json
import stat

import pytest

from agent import anthropic_credentials as credentials
from hermes_security import io as state_io, vault
from hermes_security.errors import VaultIntegrityError, VaultLockedError
from hermes_security.migrate import migrate_home


@pytest.fixture
def migrated_home(tmp_path, monkeypatch):
    home = tmp_path / "owner"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    (home / "config.yaml").write_text("model:\n  provider: anthropic\nauth:\n  adopt_external_logins: false\n")
    (home / ".env").write_text("")
    path = home / ".anthropic_oauth.json"
    path.write_text(json.dumps({"accessToken": "synthetic-original", "refreshToken": "synthetic-refresh"}))
    sidecar = credentials._spent_rotation_sidecar_path(path)
    sidecar.write_text(json.dumps({"fingerprints": ["original-fingerprint"]}))
    assert migrate_home(home, "synthetic password").ok
    yield home, path, sidecar
    vault.clear_vault_cache()


def test_migrated_singleton_pool_rotation_and_spent_fingerprints(migrated_home, monkeypatch):
    from agent.credential_pool import load_pool

    home, path, sidecar = migrated_home
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr("agent.credential_sources.adopt_external_logins_enabled", lambda: False)
    assert credentials.read_hermes_oauth_credentials()["accessToken"] == "synthetic-original"
    pool = load_pool("anthropic")
    assert any(row.source == "hermes_pkce" and row.access_token == "synthetic-original" for row in pool.entries())
    assert credentials._read_spent_rotation_sidecar(path) == {"original-fingerprint"}
    credentials._write_hermes_oauth_credentials("synthetic-rotated", "synthetic-next", 123456)
    credentials._append_spent_rotation_sidecar(path, ["next-fingerprint"])
    assert credentials.read_hermes_oauth_credentials()["accessToken"] == "synthetic-rotated"
    assert credentials._read_spent_rotation_sidecar(path) == {"original-fingerprint", "next-fingerprint"}
    for target in (path, sidecar):
        assert target.read_bytes().startswith(b"HRMVAULT\x00")
        assert b"synthetic-" not in target.read_bytes()
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    vault.clear_vault_cache()
    vault.unlock(home, "synthetic password")
    assert state_io.read_json(path, purpose="state")["refreshToken"] == "synthetic-next"


@pytest.mark.parametrize("failure", ["locked", "tampered", "denied"])
def test_managed_credentials_and_sidecars_fail_closed(migrated_home, monkeypatch, failure):
    _home, path, sidecar = migrated_home
    before = {target: target.read_bytes() for target in (path, sidecar)}
    expected = VaultIntegrityError
    if failure == "locked":
        vault.clear_vault_cache()
        expected = VaultLockedError
    elif failure == "tampered":
        for target in (path, sidecar):
            raw = before[target]
            target.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    else:
        def denied(*args, **kwargs):
            raise PermissionError("synthetic denied read")
        monkeypatch.setattr(state_io, "read_json", denied)
        expected = PermissionError
    current = {target: target.read_bytes() for target in (path, sidecar)}
    with pytest.raises(expected):
        credentials.read_hermes_oauth_credentials()
    with pytest.raises(expected):
        credentials._read_spent_rotation_sidecar(path)
    with pytest.raises(expected):
        credentials._append_spent_rotation_sidecar(path, ["must-not-replace"])
    assert {target: target.read_bytes() for target in (path, sidecar)} == current


def test_external_plaintext_credentials_keep_private_atomic_compatibility(tmp_path):
    path = tmp_path / "external" / "credentials.json"
    credentials._atomic_write_private_json(path, {"claudeAiOauth": {"accessToken": "external-synthetic"}})
    assert credentials._load_json_if_exists(path, "external")["claudeAiOauth"]["accessToken"] == "external-synthetic"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.write_bytes(b"\x86not-json")
    assert credentials._load_json_if_exists(path, "external") is None
    path.write_text("not json")
    assert credentials._load_json_if_exists(path, "external") is None
