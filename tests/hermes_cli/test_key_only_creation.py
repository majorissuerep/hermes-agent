"""Key-only creation requires a new credential outside every encrypted home."""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.platforms("linux", "macos")


def _snapshot(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).digest()
            for path in root.rglob("*") if path.is_file() and not path.is_symlink()}


def _args(output):
    return SimpleNamespace(vault_command="migrate", yes=True, key_only=True, key_out=output)


def _home_alias(root, home, _marker):
    alias = root / "home-alias"
    alias.symlink_to(home, target_is_directory=True)
    return alias / "new.key"


def _linked_output(root, marker, *, symbolic):
    output = root / "new.key"
    if symbolic:
        output.symlink_to(marker)
    else:
        output.hardlink_to(marker)
    return output


def _other_vault(root, _home, _marker):
    from hermes_security import vault

    foreign = root / "other-profile"
    vault.init_vault(foreign, "synthetic other credential")
    return foreign / "new.key"


_DESTINATIONS = {
    "default": lambda root, home, marker: None,
    "inside": lambda root, home, marker: home / "new.key",
    "home-alias": _home_alias,
    "existing": lambda root, home, marker: marker,
    "symlink": lambda root, home, marker: _linked_output(root, marker, symbolic=True),
    "hardlink": lambda root, home, marker: _linked_output(root, marker, symbolic=False),
    "other-vault": _other_vault,
}


@pytest.mark.parametrize("kind", ["default", "inside", "home-alias", "existing", "symlink", "hardlink", "other-vault"])
def test_unsafe_key_destination_refuses_without_mutating_user_data(tmp_path, monkeypatch, kind):
    from hermes_cli.vault_cmd import cmd_vault
    from hermes_security import vault

    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    home = tmp_path / "state"
    home.mkdir()
    (home / "config.yaml").write_text("agent: {max_turns: 19}\n")
    (tmp_path / "retained-backup.tar").write_bytes(b"synthetic preexisting recovery archive")
    marker = tmp_path / "retained-credential"
    marker.write_bytes(b"synthetic preexisting credential must survive")
    output = _DESTINATIONS[kind](tmp_path, home, marker)
    monkeypatch.setenv("HERMES_HOME", str(home))
    before = _snapshot(tmp_path)
    try:
        result = cmd_vault(_args(str(output) if output is not None else None))
        unchanged = _snapshot(tmp_path) == before
        assert result == 2
        assert unchanged
        assert not vault.vault_exists(home)
    finally:
        vault.clear_vault_cache()


def test_external_key_bootstraps_after_cache_loss_and_is_never_overwritten(tmp_path, monkeypatch):
    from hermes_cli.vault_cmd import cmd_vault
    from hermes_security import vault

    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    home = tmp_path / "first"
    home.mkdir()
    (home / "config.yaml").write_text("agent: {max_turns: 19}\n")
    key = tmp_path / "explicit-credential.key"
    monkeypatch.setenv("HERMES_HOME", str(home))
    try:
        assert cmd_vault(_args(str(key))) == 0
        assert key.stat().st_mode & 0o777 == 0o600
        vault.clear_vault_cache()
        monkeypatch.setenv("HERMES_VAULT_PRIVATE_KEY", str(key))
        owner = vault.get_vault(home)
        assert b"max_turns" in owner.read_bytes(home / "config.yaml", purpose="config")
        vault.clear_vault_cache()
        monkeypatch.delenv("HERMES_VAULT_PRIVATE_KEY")
        other = tmp_path / "second"
        other.mkdir()
        (other / "retained.txt").write_text("synthetic second profile")
        monkeypatch.setenv("HERMES_HOME", str(other))
        before = _snapshot(tmp_path)
        result = cmd_vault(_args(str(key)))
        unchanged = _snapshot(tmp_path) == before
        assert result == 2
        assert unchanged
        assert not vault.vault_exists(other)
    finally:
        vault.clear_vault_cache()
