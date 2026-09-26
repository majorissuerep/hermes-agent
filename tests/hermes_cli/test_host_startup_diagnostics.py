"""A detached host dying at startup must fail promptly without leaking child output."""
import json
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from hermes_cli import session_host
from hermes_security import io, vault


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "machine-home"
    vault.init_vault(path, "synthetic-host-diagnostic")
    vault.unlock(path, "synthetic-host-diagnostic")
    monkeypatch.setattr("hermes_constants.get_default_hermes_root", lambda: path)
    monkeypatch.setattr(session_host, "_POLL_S", 0.01)
    try:
        yield path
    finally:
        vault.clear_vault_cache()


def test_early_child_exit_has_private_receipt_and_does_not_wait_deadline(home, monkeypatch):
    child = subprocess.Popen([sys.executable, "-c", "import sys; print('secret-canary', file=sys.stderr); sys.exit(7)"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    monkeypatch.setattr(session_host, "_spawn_host", lambda: child)
    monkeypatch.setattr(session_host, "host_ws_url", lambda: None)
    started = time.monotonic()
    try:
        with pytest.raises(session_host.HostUnavailable, match="exit code 7") as exc:
            session_host.ensure_host(timeout_s=10)
        assert time.monotonic() - started < 5
        assert "secret-canary" not in str(exc.value)
        path = home / "runtime" / "session-host-startup.json"
        raw = path.read_bytes()
        assert raw.startswith(b"HRMVAULT\x00")
        assert b"secret-canary" not in raw
        text = io.read_text(path, purpose="state")
        assert text is not None
        receipt = json.loads(text)
        assert receipt["status"] == "exited"
        assert receipt["exit_code"] == 7
        assert receipt["pid"] == child.pid
        assert "secret-canary" not in text
    finally:
        child.wait(timeout=10)


@pytest.mark.parametrize("ready", [True, False])
def test_ready_and_timeout_are_distinct_without_recording_authenticated_url(home, monkeypatch, ready):
    urls = iter([None, "ws://fixture/?token=secret-canary" if ready else None])
    monkeypatch.setattr(session_host, "host_ws_url", lambda: next(urls, None))
    monkeypatch.setattr(session_host, "_spawn_host", lambda: SimpleNamespace(pid=123, poll=lambda: None))
    if ready:
        assert session_host.ensure_host(timeout_s=1) == "ws://fixture/?token=secret-canary"
    else:
        with pytest.raises(session_host.HostUnavailable, match="within"):
            session_host.ensure_host(timeout_s=0.02)
    text = io.read_text(home / "runtime" / "session-host-startup.json", purpose="state")
    assert text is not None and "secret-canary" not in text
    assert json.loads(text)["status"] == ("ready" if ready else "timeout")
