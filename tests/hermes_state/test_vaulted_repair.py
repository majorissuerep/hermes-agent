"""Doctor and the repair ladder use the same SQLCipher boundary as SessionDB."""
import contextlib

import pytest

from hermes_security import vault, sqlite
from hermes_state import SessionDB


@pytest.fixture
def database(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault.init_vault(home, "repair-vault-password")
    vault.unlock(home, "repair-vault-password")
    path = home / "state.db"
    db = SessionDB(db_path=path)
    db.create_session("vault-health", source="cli")
    db.append_message("vault-health", "user", "encrypted repair canary")
    db.close()
    try:
        yield path
    finally:
        vault.clear_vault_cache()


@pytest.mark.parametrize("held", [False, True])
def test_doctor_does_not_report_encryption_as_corruption(database, held):
    from hermes_cli.doctor_state import _state_db_health
    from hermes_cli.doctor_platform import _read_journal_mode
    from hermes_cli.doctor_report import Finding

    with contextlib.ExitStack() as stack:
        if held:
            holder = stack.enter_context(contextlib.closing(sqlite.connect(database)))
            holder.execute("BEGIN IMMEDIATE")
        finding = Finding()
        _state_db_health(finding, False, database, "test home")
        assert not finding.issues and not finding.manual_issues
    mode, error = _read_journal_mode(database)
    assert mode is None and "encrypted" in error
    with contextlib.closing(sqlite.connect(database)) as conn:
        assert conn.execute("SELECT content FROM messages").fetchall() == [("encrypted repair canary",)]


def test_encrypted_fts_repair_preserves_messages_and_encrypted_scratch(database):
    from hermes_state_repair import _db_opens_cleanly, repair_state_db_schema, state_db_has_structural_damage

    with contextlib.closing(sqlite.connect(database)) as conn:
        conn.execute("UPDATE messages_fts_data SET block = X'DEADBEEFDEADBEEFDEADBEEFDEADBEEF'")
        conn.commit()
    assert not state_db_has_structural_damage(database)
    assert _db_opens_cleanly(database) is not None
    result = repair_state_db_schema(database)
    assert result["repaired"], result
    assert _db_opens_cleanly(database) is None
    with contextlib.closing(sqlite.connect(database)) as conn:
        assert conn.execute("SELECT content FROM messages").fetchall() == [("encrypted repair canary",)]
    assert not list(database.parent.glob("*.repair-scratch*"))
    assert b"encrypted repair canary" not in database.read_bytes()
