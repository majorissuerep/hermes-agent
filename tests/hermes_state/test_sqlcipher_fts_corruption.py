"""SQLCipher's generic FTS error must not turn a healthy transcript into a corrupt store."""
import secrets
import sqlite3

import pytest

pytestmark = pytest.mark.platforms("linux", "macos")


@pytest.fixture
def encrypted_store(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_security import vault
    from hermes_state import SessionDB

    home = tmp_path / "home"
    password = secrets.token_urlsafe(24)
    vault.init_vault(home, password)
    vault.unlock(home, password)
    monkeypatch.setenv("HERMES_HOME", str(home))
    token = set_hermes_home_override(home)
    db = SessionDB(db_path=home / "state.db")
    assert db._fts_enabled
    db.create_session("owned", source="cli")
    for index in range(60):
        db.append_message("owned", "user", f"canonical row {index} " + "z" * 200)
    try:
        yield db, home / "state.db"
    finally:
        db.close()
        reset_hermes_home_override(token)
        vault.lock_now(home)


def test_encrypted_fts_shadow_corruption_preserves_exact_canonical_append(encrypted_store):
    from hermes_security import sqlite as cipher
    from hermes_state import SessionDB

    db, path = encrypted_store
    raw = cipher.connect(path)
    try:
        raw.execute("UPDATE messages_fts_data SET block=X'DEADBEEFDEADBEEFDEADBEEFDEADBEEF'")
        raw.commit()
        print("physical integrity after FTS-only stomp:", raw.execute("PRAGMA integrity_check").fetchall())
    finally:
        raw.close()
    try:
        db.append_message("owned", "assistant", "FTS_RECOVERY_CANONICAL_ORACLE")
    except sqlite3.DatabaseError as exc:
        print("SQLCipher append error metadata:", vars(exc))
        raise
    assert not db._db_corrupt
    db.close()
    reopened = SessionDB(db_path=path)
    try:
        rows = reopened._conn.execute("SELECT content FROM messages ORDER BY id").fetchall()
        assert len(rows) == 61
        assert rows[-1][0] == "FTS_RECOVERY_CANONICAL_ORACLE"
        assert reopened._conn.execute(
            "SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'FTS_RECOVERY_CANONICAL_ORACLE'"
        ).fetchone()[0] == 1
        assert b"FTS_RECOVERY_CANONICAL_ORACLE" not in path.read_bytes()
    finally:
        reopened.close()


def test_real_cipher_page_damage_remains_fail_closed_without_replacement(encrypted_store):
    from hermes_state import SessionDB

    db, path = encrypted_store
    db.close()
    damaged = bytearray(path.read_bytes())
    damaged[200] ^= 0x40
    path.write_bytes(damaged)
    with pytest.raises(sqlite3.DatabaseError):
        SessionDB(db_path=path)
    assert path.read_bytes() == bytes(damaged)
