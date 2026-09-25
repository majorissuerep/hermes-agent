"""Fork regression: open_db must pair SQLCipher connections with SQLCipher's Row.

Incident (luoman-MS73-HB1, 2026-09-25): a vaulted home routes cron/executions.db (and every
other ``hermes_cli.sqlite_util.open_db`` store) to SQLCipher, but ``open_db`` unconditionally
set ``row_factory = sqlite3.Row`` — stdlib Row rejects sqlcipher3 cursors, so every cron
tick's ``PRAGMA journal_mode`` probe and hosted-room worker died with
``TypeError: Row() argument 1 must be sqlite3.Cursor, not sqlcipher3.dbapi2.Cursor``.
``hermes_state.py`` already solved this shape (``_vaulted_row_factory``); ``open_db`` is the
same bug class for the small per-profile stores.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pytest

from hermes_security import migrate as mig
from hermes_security import vault as hv

PW = "open-db-row-pw"


@pytest.fixture()
def sealed_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    (home / "cron").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert mig.migrate_home(home, PW).ok
    hv.clear_vault_cache()
    hv.unlock(home, PW)
    yield home
    hv.clear_vault_cache()


def test_open_db_reads_rows_from_a_vaulted_db(sealed_home):
    from hermes_cli.sqlite_util import open_db

    db = sealed_home / "cron" / "executions.db"

    def _init(conn) -> None:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO t (name) VALUES ('incident')")

    conn = open_db(db, db_label="cron/executions.db", wal=True, initialize=_init)
    try:
        row = conn.execute("SELECT id, name FROM t").fetchone()
        # The incident's failure was HERE: stdlib Row(...) raised on an sqlcipher cursor.
        assert row["name"] == "incident"
        assert row[0] == 1
    finally:
        conn.close()


def test_open_db_plain_home_keeps_stdlib_row(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    from hermes_cli.sqlite_util import open_db

    db = tmp_path / "plain" / "store.db"

    def _init(conn) -> None:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('ok')")

    conn = open_db(db, db_label="plain/store.db", wal=True, initialize=_init)
    try:
        import sqlite3

        assert conn.row_factory is sqlite3.Row
        assert conn.execute("SELECT v FROM t").fetchone()["v"] == "ok"
    finally:
        conn.close()
