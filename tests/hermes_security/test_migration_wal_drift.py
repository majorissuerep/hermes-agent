"""Read-only subject heldout: committed WAL-only drift must not mutate an interrupted DB."""
import hashlib
from pathlib import Path
import sqlite3
import pytest

pytestmark = pytest.mark.platforms("linux", "macos")
from hermes_security import migrate, migration_workflow, vault


def test_wal_only_drift_refuses_before_conversion(tmp_path, monkeypatch):
    home = tmp_path / 'home'; home.mkdir(mode=0o700)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(vault, '_SCRYPT_N', 1024)
    database = home / 'state.db'
    connection = sqlite3.connect(database)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)')
    connection.commit(); connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    connection.execute("INSERT INTO sample VALUES(1,'original synthetic row')");connection.commit()
    original = migration_workflow._convert_member
    def stop_before_conversion(*args):
        raise KeyboardInterrupt('synthetic stop after encrypted backup; before any conversion')
    monkeypatch.setattr(migration_workflow, '_convert_member', stop_before_conversion)
    try:
        with pytest.raises(KeyboardInterrupt):
            migrate.migrate_home(home, 'public synthetic passphrase')
        main_before = hashlib.sha256(database.read_bytes()).digest()
        connection.execute("INSERT INTO sample VALUES(2,'later synthetic row')");connection.commit()
        assert hashlib.sha256(database.read_bytes()).digest() == main_before
        wal = database.with_name('state.db-wal')
        before = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (database,wal)}
        monkeypatch.setattr(migration_workflow, '_convert_member', original)
        report = migrate.migrate_home(home, 'public synthetic passphrase')
        assert not report.ok, 'Logical WAL drift must be refused'
        unchanged = all(p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==before[p.name]
                        for p in (database,wal))
        assert unchanged, 'WAL logical drift was detected only after original database conversion/mutation'
    finally:
        connection.close();vault.clear_vault_cache()
