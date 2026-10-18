"""Durable process receipts are user content, not plaintext diagnostic logs."""

import secrets

import pytest


def test_completed_result_is_encrypted_and_only_owner_can_recover(tmp_path, monkeypatch):
    from agent.session_context import scoped_current_session_id
    from hermes_security import vault
    from hermes_security.errors import PlaintextStateError
    from tools import process_registry_results as receipts
    from tools.process_registry import ProcessSession

    home = tmp_path / "profile"
    password = secrets.token_urlsafe(24)
    vault.init_vault(home, password)
    vault.unlock(home, password)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(receipts, "get_hermes_home", lambda: home)
    session = ProcessSession(
        id="proc_vault_receipt", command="echo PRIVATE_COMMAND_ORACLE",
        parent_session_id="receipt-owner", output_buffer="PRIVATE_OUTPUT_ORACLE",
        exited=True, exit_code=7,
    )
    receipts.save_completed_result(session)
    path = home / "logs" / "process-results" / f"{session.id}.json"
    raw = path.read_bytes()
    assert raw.startswith(b"HRMVAULT\x00")
    assert b"PRIVATE_COMMAND_ORACLE" not in raw
    assert b"PRIVATE_OUTPUT_ORACLE" not in raw
    with scoped_current_session_id("receipt-owner"):
        recovered = receipts.load_completed_results()[session.id]
        assert recovered.command == session.command
        assert recovered.output_buffer == session.output_buffer
        assert recovered.exit_code == 7
        path.write_text('{"id":"proc_vault_receipt"}', encoding="utf-8")
        with pytest.raises(PlaintextStateError):
            receipts.load_completed_results()
