"""A repair installer preserves encrypted logs before and after its unlock."""
import errno
import os
from pathlib import Path
import pty
import select
import subprocess
import time

import pytest

from hermes_security import frames, vault

pytestmark = pytest.mark.platforms("posix")
INSTALLER = Path(__file__).resolve().parents[2] / "scripts/install.sh"


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for name in ("CI", "GITHUB_ACTIONS", "HERMES_INSTALL_VERBOSE", "__HERMES_INSTALL_LOG_FD"):
        monkeypatch.delenv(name, raising=False)
    vault.init_vault(home, "synthetic-log-password")
    vault.unlock(home, "synthetic-log-password")
    frames.append(home / "logs/install.log", b"retained earlier diagnostic\n", purpose="log")
    return home


def _command(home, exit_code=0):
    script = '''source "$1" --manifest --hermes-home "$2"
run_logged 'Install log probe' sh -c 'printf "stdout marker\\n"; printf "stderr marker\\n" >&2; exit "$1"' probe "$3"
'''
    return ["bash", "-c", script, "installer-log-test", str(INSTALLER), str(home), str(exit_code)]


def _terminal(command):
    master, slave = pty.openpty()
    process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave)
    os.close(slave)
    output = bytearray()
    deadline = time.monotonic() + 10
    try:
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError("installer logging probe did not exit")
            if not select.select([master], [], [], 0.1)[0]:
                if process.poll() is not None:
                    break
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            output.extend(chunk)
        return process.wait(timeout=5), output.decode("utf-8", errors="replace")
    finally:
        os.close(master)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


@pytest.mark.parametrize("new_nested_home", [False, True])
def test_pre_unlock_terminal_diagnostics_never_append_plaintext_to_ciphertext(tmp_path, monkeypatch, new_nested_home):
    home = _home(tmp_path, monkeypatch)
    log = home / "logs/install.log"
    before = log.read_bytes()
    target = home / "profiles/new-home" if new_nested_home else home
    try:
        result, output = _terminal(_command(target))
        assert result == 0 and "stdout marker" in output and "stderr marker" in output
        assert log.read_bytes() == before
        if new_nested_home:
            assert not (target / "logs/install.log").exists()
    finally:
        vault.lock_now(home)


@pytest.mark.parametrize("exit_code", [0, 7])
def test_unlocked_install_diagnostics_append_complete_encrypted_frames_even_on_failure(tmp_path, monkeypatch, exit_code):
    from hermes_cli.source_install_session import run_install_session
    from pm.runtime import runtime_environment

    home = _home(tmp_path, monkeypatch)
    log = home / "logs/install.log"
    before = log.read_bytes()
    try:
        assert run_install_session(_command(home, exit_code)) == exit_code
        raw = log.read_bytes()
        assert raw.startswith(before)
        assert b"stdout marker" not in raw and b"stderr marker" not in raw
        payloads = frames._complete_stream(raw, vault=vault.get_vault(home), purpose="log")
        text = b"".join(payloads)
        assert b"retained earlier diagnostic" in text and b"stdout marker" in text and b"stderr marker" in text
        monkeypatch.setenv("__HERMES_INSTALL_LOG_FD", "123")
        assert "__HERMES_INSTALL_LOG_FD" not in runtime_environment()
    finally:
        vault.lock_now(home)
