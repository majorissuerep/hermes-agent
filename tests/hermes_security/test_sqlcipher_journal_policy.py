"""Encrypted databases obey their own SQLite runtime, not the host interpreter's."""
import sqlite3

from hermes_security import vault


def test_sqlcipher_journal_policy_uses_the_connection_driver(tmp_path, monkeypatch):
    from hermes_cli.sqlite_runtime import is_sqlite_wal_reset_vulnerable
    from hermes_security.sqlite import connect
    from hermes_state.wal import apply_wal_with_fallback

    home = tmp_path / "vaulted"
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault.init_vault(home, "synthetic-journal-policy-password")
    vault.unlock(home, "synthetic-journal-policy-password")
    # A new safe Python must not enable unsafe WAL in an older SQLCipher wheel.
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (999, 0, 0))
    conn = connect(home / "state.db")
    try:
        native = tuple(map(int, conn.execute("SELECT sqlite_version()").fetchone()[0].split(".")))
        expected = "delete" if is_sqlite_wal_reset_vulnerable(native) else "wal"
        assert apply_wal_with_fallback(conn, db_label="cipher-runtime-policy") == expected
        conn.execute("CREATE TABLE durable (value TEXT)")
        conn.execute("INSERT INTO durable VALUES ('preserved')")
        conn.commit()
    finally:
        conn.close()
    try:
        reopened = connect(home / "state.db")
        assert reopened.execute("SELECT value FROM durable").fetchone()[0] == "preserved"
        reopened.close()
    finally:
        vault.clear_vault_cache()
