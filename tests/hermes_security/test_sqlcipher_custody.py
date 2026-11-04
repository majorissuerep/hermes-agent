"""Native retained SQLCipher handles cannot outlive their original custody."""
import pytest

from hermes_security import sqlite, vault
from hermes_security.errors import VaultLockedError

PASSWORD = "synthetic database custody credential"


@pytest.fixture
def home(tmp_path, monkeypatch):
    selected = tmp_path / "owner"
    monkeypatch.setenv("HERMES_HOME", str(selected))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(selected, PASSWORD)
    vault.unlock(selected, PASSWORD)
    yield selected
    vault.clear_vault_cache()


@pytest.mark.parametrize("revoke", ["lock", "clear", "reunlock"])
def test_cached_statements_and_all_retained_cursor_reads_refuse_revocation(home, revoke):
    connection = sqlite.connect(home / "probe.db", cached_statements=32)
    connection.execute("CREATE TABLE payload (value INTEGER)")
    connection.executemany("INSERT INTO payload VALUES (?)", [(1,), (2,)])
    connection.commit()
    query = "SELECT value FROM payload ORDER BY value"
    cursors = [connection.execute(query) for _ in range(6)]
    for cursor in cursors:
        assert cursor.fetchone() == (1,)
    # Warm both the native prepared-statement cache and a separately retained cursor.
    reusable = connection.cursor()
    assert reusable.execute("SELECT count(*) FROM payload").fetchone() == (2,)
    if revoke == "clear":
        vault.clear_vault_cache()
    else:
        vault.lock_now(home)
    if revoke == "reunlock":
        vault.unlock(home, PASSWORD)
    attempts = [
        lambda: connection.execute(query),
        lambda: reusable.execute("SELECT count(*) FROM payload"),
        lambda: connection.executemany("INSERT INTO payload VALUES (?)", [(3,)]),
        lambda: connection.executescript("INSERT INTO payload VALUES (4);"),
        cursors[0].fetchone, lambda: cursors[1].fetchmany(1), cursors[2].fetchall,
        lambda: next(cursors[3]), lambda: iter(cursors[4]), lambda: cursors[5].description,
        lambda: reusable.rowcount, lambda: reusable.lastrowid, lambda: connection.total_changes,
    ]
    try:
        for attempt in attempts:
            with pytest.raises(VaultLockedError):
                attempt()
        connection.rollback()
        connection.interrupt()
    finally:
        for cursor in (*cursors, reusable):
            cursor.close()
        connection.close()
    vault.unlock(home, PASSWORD)
    with sqlite.connect(home / "probe.db") as fresh:
        assert fresh.execute("SELECT count(*) FROM payload").fetchone() == (2,)
    fresh.close()


@pytest.mark.parametrize("finish", ["commit", "context", "isolation"])
def test_revocation_refuses_implicit_and_explicit_commits_but_allows_rollback(home, finish):
    connection = sqlite.connect(home / "transaction.db")
    connection.execute("CREATE TABLE payload (value INTEGER)")
    connection.commit()
    connection.execute("INSERT INTO payload VALUES (1)")
    vault.lock_now(home)
    try:
        with pytest.raises(VaultLockedError):
            if finish == "commit":
                connection.commit()
            elif finish == "context":
                connection.__exit__(None, None, None)
            else:
                connection.isolation_level = None
        connection.rollback()
    finally:
        connection.close()
    vault.unlock(home, PASSWORD)
    fresh = sqlite.connect(home / "transaction.db")
    try:
        assert fresh.execute("SELECT count(*) FROM payload").fetchone() == (0,)
    finally:
        fresh.close()


def test_custom_native_factories_tracking_and_rows_survive_without_per_query_key_exports(home, monkeypatch):
    from hermes_cli.sqlite_safe_read import connect_tracked, has_live_connection
    driver = sqlite._sqlcipher_module()

    class CustomConnection(driver.Connection):
        calls = 0

        def execute(self, *args, **kwargs):
            self.calls += 1
            return super().execute(*args, **kwargs)

    class CustomCursor(driver.Cursor):
        pass

    path = home / "factory.db"
    connection = connect_tracked(path, connect_fn=sqlite.connect, factory=CustomConnection)
    owner = vault.get_vault(home)
    monkeypatch.setattr(type(owner._custody), "export_copy", lambda self: pytest.fail("query copied raw vault key"))
    try:
        assert isinstance(connection, driver.Connection) and isinstance(connection, CustomConnection)
        assert has_live_connection(path)
        connection.row_factory = driver.Row
        cursor = connection.cursor(factory=CustomCursor)
        assert isinstance(cursor, CustomCursor) and isinstance(cursor, driver.Cursor)
        assert cursor.execute("SELECT 7 AS value").fetchone()["value"] == 7
        assert connection.execute("SELECT 9 AS value").fetchone()["value"] == 9
        assert connection.calls > 0
        cursor.close()
    finally:
        connection.close()
    assert not has_live_connection(path)


def test_retained_incremental_blob_and_backup_cannot_bypass_revocation(home):
    connection = sqlite.connect(home / "blob.db")
    connection.execute("CREATE TABLE payload (value BLOB)")
    connection.execute("INSERT INTO payload VALUES (zeroblob(4))")
    connection.commit()
    blob = connection.open_blob("payload", "value", 1)
    destination = sqlite.connect(home / "copy.db")
    try:
        vault.lock_now(home)
        for operation in (lambda: blob.read(1), lambda: len(blob), lambda: connection.backup(destination)):
            with pytest.raises(VaultLockedError):
                operation()
    finally:
        blob.close()
        connection.close()
        destination.close()
