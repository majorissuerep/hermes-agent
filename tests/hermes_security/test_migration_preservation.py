"""Migration preserves database meaning and privately creates a usable rollback."""
import os
import sqlite3
import stat

import pytest

from hermes_security import migrate, sqlite as hsql, vault


@pytest.mark.parametrize("name", ["home", "home'quoted"])
def test_sqlcipher_export_preserves_application_metadata_and_schema(tmp_path, name):
    home = tmp_path / name
    home.mkdir()
    path = home / "state.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX sample_value ON sample(value);
CREATE VIEW sample_view AS SELECT value FROM sample;
CREATE TRIGGER sample_insert AFTER INSERT ON sample BEGIN UPDATE sample SET value=upper(value) WHERE id=new.id; END;
PRAGMA user_version=42;
PRAGMA application_id=12345;
INSERT INTO sample VALUES (1, 'preserved');
""")
    schema = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    conn.close()
    try:
        report = migrate.migrate_home(home, "synthetic-migration")
        assert report.ok, report.failures
        vault.clear_vault_cache()
        vault.unlock(home, "synthetic-migration")
        conn = hsql.connect(path)
        try:
            assert conn.execute("PRAGMA user_version").fetchone() == (42,)
            assert conn.execute("PRAGMA application_id").fetchone() == (12345,)
            assert conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall() == schema
            assert conn.execute("SELECT value FROM sample_view").fetchall() == [("PRESERVED",)]
        finally:
            conn.close()
    finally:
        vault.clear_vault_cache()


@pytest.mark.platforms("linux")
def test_rollback_is_private_during_first_write_and_remains_restorable(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("model: synthetic-rollback\n", encoding="utf-8")
    observed = []
    from hermes_security.migration_archive import _Sink

    original_add = _Sink._raw

    def add_checked(self, *args, **kwargs):
        observed.append(stat.S_IMODE(os.fstat(self.fd).st_mode))
        return original_add(self, *args, **kwargs)

    monkeypatch.setattr(_Sink, "_raw", add_checked)
    vault.init_vault(home, "synthetic-rollback", _allow_existing_state=True)
    vault.unlock(home, "synthetic-rollback")
    old_umask = os.umask(0o022)
    try:
        first = migrate._backup_home(home, tmp_path)
        second = migrate._backup_home(home, tmp_path)
    finally:
        os.umask(old_umask)
    assert observed and all(mode == 0o600 for mode in observed)
    assert first != second, "two backups must never overwrite the rollback copy"
    for path in (first, second):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        from hermes_security.profile_binding import capture
        from hermes_security.migration_archive import verified_tar

        with verified_tar(path, custody=capture(home)) as archive:
            member = next(iter(archive))
            assert member.name == "config.yaml"
            assert archive.extractfile(member).read() == b"model: synthetic-rollback\n"
