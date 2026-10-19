"""A writable data volume can migrate without making its immutable parent writable."""

import os
import secrets
import stat
import tarfile

import pytest

from hermes_security import io, migrate, vault


@pytest.fixture
def volume(tmp_path, monkeypatch):
    parent = tmp_path / "immutable"
    home = parent / "data"
    home.mkdir(parents=True, mode=0o700)
    backup = parent / "data-migration-backups"
    backup.mkdir(mode=0o700)
    original = ("VOLUME_CANARY=" + secrets.token_hex(24) + "\n").encode()
    (home / ".env").write_bytes(original)
    monkeypatch.setenv("HERMES_HOME", str(home))
    parent.chmod(0o555)
    try:
        if os.access(parent, os.W_OK):
            pytest.skip("this native identity can override the parent's read-only mode")
        yield home, backup, original
    finally:
        parent.chmod(0o755)
        vault.clear_vault_cache()


@pytest.mark.platforms("posix")
def test_volume_migration_keeps_private_rollback_and_recovers_damaged_env(volume):
    home, backup, original = volume
    report = migrate.migrate_home(home, "synthetic-volume-password")
    assert report.ok, report.failures
    assert report.backup_path.parent == backup
    assert stat.S_IMODE(report.backup_path.stat().st_mode) == 0o600
    with tarfile.open(report.backup_path) as archive:
        assert archive.extractfile(".env").read() == original
    assert (home / ".env").read_bytes().startswith(b"HRMVAULT\x00")
    assert io.read_bytes(home / ".env", purpose="env") == original
    damaged = bytearray((home / ".env").read_bytes())
    damaged[-1] ^= 1
    (home / ".env").write_bytes(damaged)
    repaired = migrate.repair_clobbered_state(home)
    assert repaired["restored"] and not repaired["unrecoverable"], repaired
    assert io.read_bytes(home / ".env", purpose="env") == original
    assert not migrate.scan_for_plaintext(home)
    assert not os.access(home.parent, os.W_OK)


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("unsafe", ["public", "symlink"])
def test_volume_migration_refuses_unsafe_rollback_before_changing_state(volume, tmp_path, unsafe):
    home, backup, original = volume
    if unsafe == "public":
        backup.chmod(0o777)
    else:
        home.parent.chmod(0o755)
        backup.rmdir()
        target = tmp_path / "redirect"
        target.mkdir(mode=0o700)
        backup.symlink_to(target, target_is_directory=True)
        home.parent.chmod(0o555)
    with pytest.raises(PermissionError):
        migrate.migrate_home(home, "synthetic-volume-password")
    assert not vault.vault_exists(home)
    assert (home / ".env").read_bytes() == original
    assert list(backup.iterdir()) == []
