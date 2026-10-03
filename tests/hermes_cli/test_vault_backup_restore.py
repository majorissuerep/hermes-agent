"""Encrypted backups keep their original database key across staging and publication."""

import sqlite3
import zipfile

import pytest

from hermes_cli.backup import _zip_sqlite_snapshot, verify_sqlite_integrity
from hermes_cli.backup_restore import _import_db_member, _safe_restore_db
from hermes_cli.backup_sqlite import _safe_copy_db
from hermes_security import sqlite as vault_sqlite, vault


@pytest.fixture
def homes(tmp_path, monkeypatch):
    homes = [tmp_path / "profile A", tmp_path / "profile B"]
    for home in homes:
        home.mkdir()
        vault.init_vault(home, "backup-test-password")
        vault.unlock(home, "backup-test-password")
    monkeypatch.setenv("HERMES_HOME", str(homes[0]))
    yield homes
    vault.clear_vault_cache()


def _database(home, value):
    path = home / "state.db"
    conn = vault_sqlite.connect(path, isolation_level=None)
    conn.execute("CREATE TABLE IF NOT EXISTS messages (body TEXT)")
    conn.execute("DELETE FROM messages")
    conn.execute("INSERT INTO messages VALUES (?)", (value,))
    return path, conn


def test_snapshot_roundtrip_keeps_ciphertext_and_live_inode_across_profiles(homes, tmp_path):
    for index, home in enumerate([*homes, homes[0]]):
        value = f"profile-private-message-{index}"
        path, live = _database(home, value)
        staged, published = tmp_path / "staging.db", tmp_path / "published.db"
        try:
            assert _safe_copy_db(path, staged)
            staged.replace(published)
            assert value.encode() not in published.read_bytes()
            with sqlite3.connect(published) as plain:
                with pytest.raises(sqlite3.DatabaseError):
                    plain.execute("SELECT * FROM messages").fetchall()
            assert verify_sqlite_integrity(published, key_path=path)["valid"]
            live.execute("UPDATE messages SET body = 'after-snapshot'")
            inode = path.stat().st_ino
            assert _safe_restore_db(published, path)
            assert path.stat().st_ino == inode
            assert live.execute("SELECT body FROM messages").fetchall() == [(value,)]
        finally:
            live.close()
            published.unlink(missing_ok=True)


def test_zip_snapshot_restores_original_key_and_rejects_another_profile(homes, tmp_path):
    path, live = _database(homes[0], "archive-private-message")
    other, other_live = _database(homes[1], "other-profile-message")
    archive = tmp_path / "backup.zip"
    try:
        with zipfile.ZipFile(archive, "w") as zf:
            assert _zip_sqlite_snapshot(zf, path, path.relative_to(homes[0]), archive)
        live.execute("UPDATE messages SET body = 'after-snapshot'")
        with zipfile.ZipFile(archive) as zf:
            member = zf.getinfo("state.db")
            assert b"archive-private-message" not in zf.read(member)
            _import_db_member(zf, member, path, 0o600)
            with pytest.raises(OSError):
                _import_db_member(zf, member, other, 0o600)
        assert live.execute("SELECT body FROM messages").fetchall() == [("archive-private-message",)]
        assert other_live.execute("SELECT body FROM messages").fetchall() == [("other-profile-message",)]
    finally:
        live.close()
        other_live.close()
