"""Fork policy: the master passphrase is NEVER read from the environment.

Threat model: the system must never hold the vault's unlock credential in a place the
environment leaks (``/proc/<pid>/environ``, child inheritance, unit EnvironmentFiles).
The sanctioned non-interactive credential is a KEY FILE (``HERMES_VAULT_PRIVATE_KEY`` —
a PATH, never secret material); humans unlock on a TTY. These tests pin the inversion of
the old env-password contract.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pytest

from hermes_security import vault as hv

PW = "policy-test-passphrase"


@pytest.fixture()
def vaulted_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_MASTER_PASSWORD", raising=False)
    monkeypatch.delenv("HERMES_VAULT_PRIVATE_KEY", raising=False)
    hv.init_vault(home, PW, _allow_existing_state=True)
    # Key slot for the key-file half of the contract.
    priv, pub = hv.generate_keypair()
    hv.add_key_slot(home, public_key_raw=pub, password=PW)
    hv.clear_vault_cache()
    key_file = tmp_path / "vault.key"
    key_file.write_bytes(hv._b64e(priv).encode("ascii"))
    key_file.chmod(0o600)
    yield home, key_file
    hv.clear_vault_cache()


def test_env_passphrase_does_not_unlock(vaulted_home, monkeypatch):
    """The OLD behavior (env password unlocks) is now policy-violating: must raise."""
    home, _ = vaulted_home
    monkeypatch.setenv("HERMES_MASTER_PASSWORD", PW)  # even the CORRECT passphrase
    hv.clear_vault_cache()
    with pytest.raises(hv.VaultLockedError):
        hv.get_vault(home)


def test_env_passphrase_does_not_unlock_the_gate(vaulted_home, monkeypatch):
    """The CLI startup gate ignores a correct env passphrase, same policy."""
    from types import SimpleNamespace

    from hermes_cli import vault_gate

    home, _ = vaulted_home
    monkeypatch.setenv("HERMES_MASTER_PASSWORD", PW)
    monkeypatch.setattr(vault_gate, "_tty_available", lambda: False)
    with pytest.raises(SystemExit) as e:
        vault_gate.gate_startup(SimpleNamespace(command="config", version=False))
    assert e.value.code == 2


def test_key_file_path_unlocks(vaulted_home, monkeypatch):
    """The sanctioned daemon credential: a PATH to a 0600 key file."""
    home, key_file = vaulted_home
    monkeypatch.setenv("HERMES_VAULT_PRIVATE_KEY", str(key_file))
    hv.clear_vault_cache()
    assert hv.get_vault(home) is not None
    assert hv.is_unlocked(home)


def test_key_only_migrate_creates_a_key_file_vault(tmp_path, monkeypatch, capsys):
    """Non-interactive creation: --key-only produces a vault whose ONLY credential is
    the private key file; no passphrase ever exists for the caller."""
    from types import SimpleNamespace

    from hermes_cli import vault_cmd

    home = tmp_path / ".hermes"
    home.mkdir()
    (home / ".env").write_text("X=1\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_MASTER_PASSWORD", raising=False)
    monkeypatch.delenv("HERMES_VAULT_PRIVATE_KEY", raising=False)
    key_out = tmp_path / "daemon.key"

    args = SimpleNamespace(vault_command="migrate", yes=False, key_only=True,
                           key_out=str(key_out), public_key=None, private_key=None, slot=None)
    rc = vault_cmd.cmd_vault(args)
    assert rc == 0
    assert key_out.exists() and (key_out.stat().st_mode & 0o777) == 0o600

    # Fresh process: the key file unlocks EVERYTHING; the passphrase does not exist.
    hv.clear_vault_cache()
    monkeypatch.setenv("HERMES_VAULT_PRIVATE_KEY", str(key_out))
    assert hv.get_vault(home) is not None
    hv.clear_vault_cache()
    monkeypatch.delenv("HERMES_VAULT_PRIVATE_KEY", raising=False)
    with pytest.raises(hv.VaultLockedError):
        hv.get_vault(home)


def test_noninteractive_creation_refuses_without_key_only(tmp_path, monkeypatch):
    """Plain migrate without a TTY and without --key-only exits 2 with guidance."""
    from types import SimpleNamespace

    from hermes_cli import vault_cmd

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_MASTER_PASSWORD", raising=False)
    monkeypatch.setattr(vault_cmd, "_tty_available", lambda: False)
    args = SimpleNamespace(vault_command="migrate", yes=True, key_only=False,
                           key_out=None, public_key=None, private_key=None, slot=None)
    with pytest.raises(SystemExit) as e:
        vault_cmd.cmd_vault(args)
    assert e.value.code == 2
