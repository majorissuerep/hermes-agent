"""Encrypted logs retain diagnostics and redact secrets without corrupting finite stdout."""

import contextvars
import logging
import secrets

import pytest


@pytest.fixture
def encrypted_log(tmp_path, monkeypatch):
    from agent.redact import RedactingFormatter
    import hermes_logging
    from hermes_security import frames, vault

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for name in ("HERMES_YOLO_MODE", "HERMES_ACCEPT_HOOKS", "HERMES_SINGLE_QUERY_SESSION"):
        monkeypatch.delenv(name, raising=False)
    password = secrets.token_urlsafe(24)
    vault.init_vault(tmp_path, password)
    vault.unlock(tmp_path, password)
    disabled = logging.root.manager.disable
    logging.disable(logging.NOTSET)
    path = tmp_path / "logs" / "agent.log"
    handler = hermes_logging._new_file_handler(
        path, level=logging.INFO, max_bytes=1024 * 1024, backup_count=1,
        formatter=RedactingFormatter("%(levelname)s | %(message)s"),
    )
    logger = logging.Logger("encrypted-contract", level=logging.INFO)
    logger.addHandler(handler)
    try:
        yield logger, lambda: b"".join(frames.read_frames(path, purpose="log")).decode(), path
    finally:
        logger.removeHandler(handler)
        handler.close()
        logging.disable(disabled)
        vault.clear_vault_cache()


def test_encrypted_records_apply_redaction_and_preserve_diagnostic_metadata(encrypted_log):
    logger, decoded, path = encrypted_log
    token = "sk-proj-" + secrets.token_hex(24)
    witness = secrets.token_hex(10)
    logger.warning("witness=%s credential=%s", witness, token)
    text = decoded()
    assert "WARNING | witness=" + witness in text
    assert token not in text
    assert token.encode() not in path.read_bytes()


def test_quiet_finite_turn_keeps_its_encrypted_error_log(encrypted_log, monkeypatch, capsys):
    from hermes_cli import oneshot

    logger, decoded, _ = encrypted_log
    witness = secrets.token_hex(12)

    def run_agent(*args, **kwargs):
        logger.error("turn diagnostic %s", witness)
        print("internal progress must not corrupt stdout")
        return "final answer", {"completed": True}

    monkeypatch.setattr(oneshot, "_run_agent", run_agent)
    assert contextvars.copy_context().run(oneshot._run_owned_oneshot, "test") == 0
    assert capsys.readouterr().out == "final answer\n"
    assert witness in decoded()
