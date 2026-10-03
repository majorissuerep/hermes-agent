"""Log tailing and the upstream debug snapshot reader share the vault boundary."""

import pytest

from hermes_cli import debug, logs
from hermes_security import frames, vault
from hermes_security.errors import VaultError


@pytest.fixture
def log_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    vault.init_vault(home, "log-reader-password")
    vault.unlock(home, "log-reader-password")
    monkeypatch.setenv("HERMES_HOME", str(home))
    path = home / "logs/agent.log"
    path.parent.mkdir()
    try:
        yield home, path
    finally:
        vault.clear_vault_cache()


def test_large_encrypted_logs_are_read_and_redacted_but_locked_logs_fail_closed(log_home):
    home, path = log_home
    frames.append(path, b"old line\n" * 140000, purpose="log")
    line = "2026-10-03 12:00:00 INFO agent: https://example.test/?token=private-log-secret\n"
    frames.append(path, line.encode(), purpose="log")
    assert logs._read_last_n_lines(path, 1) == [line]
    snapshot = debug._capture_log_snapshot("agent", tail_lines=1, max_bytes=2048)
    assert "example.test" in snapshot.tail_text
    assert "private-log-secret" not in snapshot.tail_text + snapshot.full_text
    assert b"private-log-secret" not in path.read_bytes()
    before = path.read_bytes()
    vault.lock_now(home)
    with pytest.raises(VaultError):
        logs._read_last_n_lines(path, 1)
    locked = debug._capture_log_snapshot("agent", tail_lines=1)
    assert locked.full_text is None
    assert "error reading" in locked.tail_text
    assert path.read_bytes() == before


def test_follow_waits_for_complete_authenticated_frames(log_home, monkeypatch, capsys):
    home, path = log_home
    frames.append(path, b"backlog\n", purpose="log")
    packet = frames.encode_frames([b"2026-10-03 12:00:00 INFO agent: new record\n"],
                                  vault=vault.get_vault(home), purpose="log")
    chunks = iter((packet[:9], packet[9:]))

    def append_next(_seconds):
        chunk = next(chunks)
        with path.open("ab") as stream:
            stream.write(chunk)

    monkeypatch.setattr(logs.time, "sleep", append_next)
    with pytest.raises(StopIteration):
        logs._follow_log(path)
    assert capsys.readouterr().out == "2026-10-03 12:00:00 INFO agent: new record\n"
