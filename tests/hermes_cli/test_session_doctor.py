"""The public operation client exposes the existing owning installation diagnostics."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.platforms("linux", "macos")
@pytest.mark.live_system_guard_bypass
def test_operation_grant_public_doctor_help_preserves_encrypted_owner(tmp_path, monkeypatch):
    from hermes_cli.runtime_launcher import write_operation_client
    from hermes_security import vault
    from hermes_security.session_authority import start_session_authority

    source = Path(__file__).resolve().parents[2]
    home = tmp_path / "owner"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(home, "synthetic doctor credential")
    owner = vault.unlock(home, "synthetic doctor credential")
    configuration = b"agent:\n  max_turns: 19\nupdates:\n  check: false\n"
    owner.write_bytes("config.yaml", configuration, purpose="config")
    owner.write_bytes(".env", b"", purpose="env")
    ciphertext = (home / "config.yaml").read_bytes()
    directory = tmp_path / "client-bin"
    directory.mkdir(mode=0o700)
    write_operation_client(directory, source)
    authority = start_session_authority(home, "doctor-help-owner")
    env = {name: value for name, value in os.environ.items()
           if name not in {"HERMES_SESSION_VAULT", "HERMES_VAULT_KEY_FD", "HERMES_VAULT_PRIVATE_KEY",
                           "HERMES_MASTER_PASSWORD"}}
    env.update(authority.environment(), HERMES_SESSION_ID=authority.session_id)
    try:
        result = subprocess.run([str(directory / "hermes"), "doctor", "--help"], env=env, cwd=source,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60, check=False)
        assert result.returncode == 0, {
            "exit_code": result.returncode,
            "command_policy_refusal": "does not authorize this native command" in result.stderr,
        }
        assert "Diagnose issues with Hermes Agent setup" in result.stdout
        assert "--fix" in result.stdout and "--live" in result.stdout
        assert (home / "config.yaml").read_bytes() == ciphertext
        assert ciphertext.startswith(b"HRMVAULT\x00")
        assert owner.read_bytes("config.yaml", purpose="config") == configuration
    finally:
        authority.close()
        vault.clear_vault_cache()
