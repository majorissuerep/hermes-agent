"""Fork regression: a vaulted-home kanban board is SQLCipher — open it as such.

Incident (luoman-MS73-HB1, 2026-09-25): the gateway's kanban dispatcher refused the sealed
board with "board default database ~/.hermes/kanban.db is not a valid SQLite database" and
paused dispatch. Two causes: the plaintext-header byte probe condemned SQLCipher ciphertext,
and ``row_factory = sqlite3.Row`` rejects sqlcipher3 cursors.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pytest

from hermes_security import migrate as mig
from hermes_security import vault as hv

PW = "kanban-vaulted-pw"


@pytest.fixture()
def sealed_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert mig.migrate_home(home, PW).ok
    hv.clear_vault_cache()
    hv.unlock(home, PW)
    yield home
    hv.clear_vault_cache()


def test_connect_opens_a_vaulted_board(sealed_home):
    from hermes_cli import kanban_db_connect as kdb

    board = sealed_home / "kanban.db"
    board.write_bytes(b"")  # fresh, empty file — connect() initializes it

    conn = kdb.connect(db_path=board)
    try:
        conn.execute("CREATE TABLE probe (v TEXT)")
        conn.execute("INSERT INTO probe VALUES ('sealed-ok')")
        row = conn.execute("SELECT v FROM probe").fetchone()
        assert row["v"] == "sealed-ok"  # the incident: Row() TypeError / "not a database"
    finally:
        conn.close()

    # On disk it is SQLCipher: no plaintext SQLite header.
    assert not board.read_bytes().startswith(b"SQLite format 3\x00")

    # A second connect (the dispatcher's shape) must pass the header probe and read rows.
    conn2 = kdb.connect(db_path=board)
    try:
        assert conn2.execute("SELECT v FROM probe").fetchone()["v"] == "sealed-ok"
    finally:
        conn2.close()
