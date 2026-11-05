"""Current encrypted browser state survives full backup, strict restore and orphan recovery."""
from __future__ import annotations

import hashlib
import os
import socket
import types
import zipfile
from pathlib import Path

import pytest

from hermes_cli import backup, browser_profile_store as store
from hermes_security import browser_profile_archive as archive, vault
from tests.hermes_cli.browser_profile_env import (
    chromium_tree, cookie_db, exited_browser, owned_profile_env, reset_owned_profiles,
)


@pytest.fixture
def world(tmp_path, monkeypatch):
    home, ram = owned_profile_env(tmp_path, monkeypatch)
    vault.get_vault(home).write_bytes(home / "config.yaml", b"agent: {max_turns: 19}\n", purpose="config")
    yield home, ram
    reset_owned_profiles()


def _profile(cookie):
    profile = store.owned_profile(archive.DEBUG_SLOT)
    with store.prepare(profile, {}) as path:
        if (path / "Default").exists():
            cookie_db(path / "Default" / "Network" / "Cookies", cookie)
        else:
            chromium_tree(path, cookie, stale_lock=False)
    store.record_launch(profile, exited_browser())
    return profile


def _saved(cookie):
    profile = _profile(cookie)
    assert store.settle() == {archive.DEBUG_SLOT: "saved"}
    path = profile.home / archive.ARCHIVE_ROOT / "chrome-debug.hbpa"
    return profile, path, path.read_bytes()


def _restore(path):
    try:
        result = backup.run_import(types.SimpleNamespace(zipfile=str(path), force=True))
        return 0 if result is None else result
    except SystemExit as error:
        return error.code


def _bundle(path, home, ciphertext):
    with zipfile.ZipFile(path, "w") as packed:
        packed.writestr(".hermes-vault", (home / ".hermes-vault").read_bytes())
        packed.writestr("config.yaml", (home / "config.yaml").read_bytes())
        packed.writestr("browser-profile/chrome-debug.hbpa", ciphertext)


def test_full_backup_and_import_reopen_exact_cookie_without_exporting_legacy_state(world, tmp_path):
    from hermes_security.recovery_export import export_home

    home, _ram = world
    profile, stored, first = _saved("first-saved-cookie")
    legacy = home / archive.ARCHIVE_ROOT / "chrome"
    legacy.mkdir()
    (legacy / "Cookies").write_bytes(b"raw-old-cookie")
    stage = stored.parent / ".stage-unverified"
    stage.write_bytes(b"raw-stage-cookie")
    debug = home / "chrome-debug"
    debug.mkdir()
    (debug / "Cookies").write_bytes(b"raw-debug-cookie")
    output = tmp_path / "full.zip"
    assert backup._write_full_zip_backup(output, home) == output
    with zipfile.ZipFile(output) as packed:
        assert packed.read("browser-profile/chrome-debug.hbpa") == first
        assert not any("raw-" in name or name.startswith("chrome-debug/") or
                       name.startswith("browser-profile/chrome/") or ".stage-" in name
                       for name in packed.namelist())
    # These unconverted copies remain untouched, but do not block a same-vault
    # archive import. A new profile start still requires explicit legacy repair.
    assert (legacy / "Cookies").read_bytes() == b"raw-old-cookie"
    recovery = tmp_path / "plaintext.tar"
    export_home(home, recovery)
    import tarfile
    with tarfile.open(recovery) as packed:
        assert not any(name.startswith(("browser-profile/", "chrome-debug/")) for name in packed.getnames())
    import shutil
    shutil.rmtree(legacy)
    shutil.rmtree(debug)
    stage.unlink()
    before = hashlib.sha256(output.read_bytes()).digest()
    _saved("newer-cookie")
    stored.chmod(0o644)
    assert _restore(output) == 0
    assert stored.read_bytes() == first and stored.stat().st_mode & 0o777 == 0o600
    assert hashlib.sha256(output.read_bytes()).digest() == before
    with store.prepare(profile, {}) as reopened:
        assert cookie_db(reopened / "Default" / "Network" / "Cookies") == "first-saved-cookie"
    # A disaster-recovery home starts with only its original metadata/credential,
    # not an already-secured browser-profile directory from the old installation.
    recovered = tmp_path / "recovered"
    recovered.mkdir()
    (recovered / ".hermes-vault").write_bytes((home / ".hermes-vault").read_bytes())
    (recovered / ".hermes-vault").chmod(0o600)
    from tests.hermes_cli.browser_profile_env import PASSWORD
    vault.unlock(recovered, PASSWORD)
    with pytest.MonkeyPatch.context() as scope:
        scope.setenv("HERMES_HOME", str(recovered))
        assert _restore(output) == 0
        restored_archive = recovered / "browser-profile" / "chrome-debug.hbpa"
        assert restored_archive.read_bytes() == first
        assert restored_archive.parent.stat().st_mode & 0o777 == 0o700
        assert restored_archive.stat().st_mode & 0o777 == 0o600
        with store.prepare(store.owned_profile(archive.DEBUG_SLOT), {}) as reopened:
            assert cookie_db(reopened / "Default" / "Network" / "Cookies") == "first-saved-cookie"
    assert stored.read_bytes() == first


def test_staged_import_authenticates_current_archive_without_rewrapping(world, tmp_path):
    from hermes_cli.backup_vault_import import prepare_import_file

    _home, _ram = world
    _profile_value, stored, ciphertext = _saved("import-cookie")
    staged = tmp_path / "staged"
    staged.write_bytes(ciphertext)
    staged.chmod(0o600)
    prepare_import_file(staged, stored)
    assert staged.read_bytes() == ciphertext
    staged.write_bytes(ciphertext[:-1])
    with pytest.raises((OSError, vault.VaultError)):
        prepare_import_file(staged, stored)
    assert stored.read_bytes() == ciphertext


def test_locked_backup_keeps_opaque_archive_and_known_damage_is_salvaged(world, tmp_path):
    home, _ram = world
    _profile_value, stored, ciphertext = _saved("locked-cookie")
    unknown = stored.with_name("retained-unknown.hbpa")
    unknown.write_bytes(b"unverified-future-browser-state")
    unknown.chmod(0o600)
    vault.lock_now(home)
    output = tmp_path / "locked.zip"
    assert backup._write_full_zip_backup(output, home) == output
    with zipfile.ZipFile(output) as packed:
        assert packed.read("browser-profile/chrome-debug.hbpa") == ciphertext
        assert packed.read("browser-profile/retained-unknown.hbpa") == unknown.read_bytes()
    from tests.hermes_cli.browser_profile_env import PASSWORD
    vault.unlock(home, PASSWORD)
    known_good = output.read_bytes()
    damaged = b"retained-unverified-browser-state"
    stored.write_bytes(damaged)
    assert backup._write_full_zip_backup(output, home) is None
    assert output.read_bytes() == known_good
    salvage = output.with_name(output.stem + backup._INCOMPLETE_ZIP_SUFFIX)
    with zipfile.ZipFile(salvage) as packed:
        assert packed.read("browser-profile/chrome-debug.hbpa") == damaged


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("busy", [False, True], ids=["quiescent-orphan", "live-browser"])
def test_restore_refuses_stale_ram_and_public_repair_recovers_only_quiescent_copy(world, tmp_path, busy, capsys):
    from hermes_cli.vault_cmd import cmd_vault

    home, _ram = world
    profile, stored, ciphertext = _saved("backup-cookie")
    source = tmp_path / "source.zip"
    _bundle(source, home, ciphertext)
    profile = _profile("unsaved-ram-cookie")
    if busy:
        (profile.path / "SingletonLock").symlink_to(f"{socket.gethostname()}-{os.getpid()}")
    store._LEASES.clear()  # the browser/RAM copy outlived its original Hermes owner
    before = archive.tree_digest(profile.path)
    assert _restore(source) == 1
    assert "Browser state still exists in RAM" in capsys.readouterr().out
    assert stored.read_bytes() == ciphertext and archive.tree_digest(profile.path) == before
    result = cmd_vault(types.SimpleNamespace(vault_command="repair"))
    if busy:
        assert result == 1 and profile.path.exists()
        assert stored.read_bytes() == ciphertext and archive.tree_digest(profile.path) == before
    else:
        assert result == 0 and not profile.path.exists()
        assert stored.read_bytes() != ciphertext  # sole RAM state was checkpointed before retirement
        assert _restore(source) == 0 and stored.read_bytes() == ciphertext
        with store.prepare(profile, {}) as reopened:
            assert cookie_db(reopened / "Default" / "Network" / "Cookies") == "backup-cookie"
