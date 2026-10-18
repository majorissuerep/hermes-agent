"""Process recovery metadata has the same vault boundary as completed receipts."""

import secrets

import pytest


def test_checkpoint_encrypts_and_recovers_exact_process_metadata(tmp_path, monkeypatch):
    from hermes_security import vault
    from hermes_security.errors import PlaintextStateError
    from tools import process_registry as processes

    home = tmp_path / "profile"
    password = secrets.token_urlsafe(24)
    vault.init_vault(home, password)
    vault.unlock(home, password)
    monkeypatch.setenv("HERMES_HOME", str(home))
    path = home / "processes.json"
    monkeypatch.setattr(processes, "CHECKPOINT_PATH", path)
    writer = processes.ProcessRegistry()
    session = processes.ProcessSession(
        id="proc_checkpoint_oracle", command="echo PRIVATE_CHECKPOINT_ORACLE",
        pid=12345, host_start_time=67890, parent_session_id="owning-conversation",
    )
    writer._running[session.id] = session
    writer._write_checkpoint()
    raw = path.read_bytes()
    assert raw.startswith(b"HRMVAULT\x00")
    assert b"PRIVATE_CHECKPOINT_ORACLE" not in raw
    assert b"owning-conversation" not in raw

    reader = processes.ProcessRegistry()
    # The test never probes, adopts, or signals a real process.
    monkeypatch.setattr(reader, "_detached_host_fate", lambda pid, start: "running")
    assert reader.recover_from_checkpoint() == 1
    recovered = reader._running[session.id]
    assert recovered.command == session.command
    assert recovered.parent_session_id == session.parent_session_id
    assert recovered.pid == session.pid
    assert recovered.host_start_time == session.host_start_time
    assert recovered.detached

    path.write_text("[]", encoding="utf-8")
    with pytest.raises(PlaintextStateError):
        reader.recover_from_checkpoint()
    with pytest.raises(PlaintextStateError):
        writer._write_checkpoint()
    assert path.read_text(encoding="utf-8") == "[]"
