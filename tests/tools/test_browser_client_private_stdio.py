"""Native client boundaries keep private command I/O off persistent spool files.

The executable is a synthetic native process, not Chromium or a provider. Its real
stdio descriptors and independently owned daemon qualify the transport boundary.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from hermes_platform.processes import get_process_start_time, terminate_pid
from tools import browser_terminal
from tools import browser_tool_lightpanda_fallback as fallback
from tools import browser_tool_real_profile as real_profile
from tools import browser_tool_session as session


def _environment(monkeypatch, directory):
    env = {**os.environ, "AGENT_BROWSER_SOCKET_DIR": str(directory), "HERMES_HOME": str(directory.parent / "owner")}
    monkeypatch.setattr(browser_terminal, "browser_in_terminal", lambda: False)
    monkeypatch.setattr(session, "_prepare_session_socket_dir", lambda name: str(directory))
    monkeypatch.setattr(session, "_agent_browser_command_env", lambda socket_dir: dict(env))
    monkeypatch.setattr(session, "_apply_chromium_sandbox_args", lambda env: None)
    monkeypatch.setattr(real_profile, "_real_profile_daemon_env", lambda: (dict(env), str(directory)))
    return env


def _invoke(route, executable, monkeypatch, *, payload=None, timeout=10):
    if route == "native":
        return session._spawn_and_collect(
            "owner", {"session_name": "owned", "features": {}}, [str(executable)],
            "batch", "lightpanda", timeout, stdin_payload=payload)
    if route == "real-profile":
        assert payload is None
        result = real_profile._capture_agent_browser_cli([str(executable)], timeout, "probe")
        assert result.returncode == 0
        return json.loads(result.stdout)
    assert route == "fallback" and payload is None
    monkeypatch.setattr(session, "_run_browser_command", lambda *args, **kwargs: {
        "success": True, "data": {"url": "https://fixture.invalid/"}})
    monkeypatch.setattr(fallback._install, "_find_agent_browser", lambda: str(executable))
    monkeypatch.setattr(fallback._install, "_chromium_installed", lambda: True)
    monkeypatch.setattr(fallback._cloud, "_is_headed_mode", lambda: False)
    return fallback._run_chrome_fallback_command("owner", "eval", ["fixture"], timeout)


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("route", ["native", "real-profile", "fallback"])
def test_native_browser_consumers_use_pipes_without_private_spool_files(tmp_path, monkeypatch, route):
    directory = tmp_path / "sockets"
    directory.mkdir(mode=0o700)
    env = _environment(monkeypatch, directory)
    executable = tmp_path / "native-client"
    executable.write_text(f"#!{sys.executable}\n" + '''import json, os, stat, sys
from pathlib import Path
directory = Path(os.environ["AGENT_BROWSER_SOCKET_DIR"])
data = sys.stdin.buffer.read() if "--engine" not in sys.argv else b""
print(json.dumps({"success": True, "data": {
    "pipes": [stat.S_ISFIFO(os.fstat(fd).st_mode) for fd in (1, 2)],
    "stdin_pipe": stat.S_ISFIFO(os.fstat(0).st_mode),
    "input": data.hex(),
    "home": os.environ["HERMES_HOME"],
    "spools": sorted(p.name for p in directory.iterdir() if p.name.startswith(("_stdin_", "_stdout_", "_stderr_")))
}}))
''')
    executable.chmod(0o700)
    payload = b"private-browser-input-\x00\xff" if route == "native" else None
    result = _invoke(route, executable, monkeypatch, payload=payload)
    assert result["success"] is True
    data = result["data"]
    assert data["spools"] == []
    assert data["pipes"] == [True, True]
    assert data["home"] == env["HERMES_HOME"]
    if payload is not None:
        assert data["stdin_pipe"] is True
        assert data["input"] == payload.hex()
    assert not directory.exists() or not any(
        p.name.startswith(("_stdin_", "_stdout_", "_stderr_")) for p in directory.iterdir())


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("timed_out", [False, True])
def test_client_exit_or_timeout_does_not_wait_for_or_kill_independent_daemon(tmp_path, monkeypatch, timed_out):
    directory = tmp_path / "sockets"
    directory.mkdir(mode=0o700)
    _environment(monkeypatch, directory)
    receipt = tmp_path / "process-identities.json"
    executable = tmp_path / "native-client"
    executable.write_text(f"#!{sys.executable}\n" + '''import json, os, signal, sys
from pathlib import Path
daemon = os.fork()
if daemon == 0:
    signal.pause()
    os._exit(0)
Path(sys.argv[1]).write_text(json.dumps({"client": os.getpid(), "daemon": daemon}))
sys.stdout.buffer.write(b"\\xef\\xbb\\xbf{\\\"success\\\":true,\\\"data\\\":{\\\"text\\\":\\\"caf\\xc3\\xa9\\\"}}\\n")
sys.stdout.flush()
sys.stderr.buffer.write(b"diagnostic-\\xff\\n"); sys.stderr.flush()
if sys.argv[2] == "timeout":
    signal.pause()
''')
    executable.chmod(0o700)
    argv = [str(executable), str(receipt), "timeout" if timed_out else "exit"]
    daemon = started = None
    try:
        if timed_out:
            with pytest.raises(subprocess.TimeoutExpired):
                real_profile._capture_agent_browser_cli(argv, 2, "lifetime")
        else:
            result = real_profile._capture_agent_browser_cli(argv, 5, "lifetime")
            assert result.returncode == 0
            assert json.loads(result.stdout)["data"]["text"] == "café"
            assert result.stderr == "diagnostic-\ufffd\n"
        identities = json.loads(receipt.read_text())
        daemon = identities["daemon"]
        started = get_process_start_time(daemon)
        assert started is not None
        assert get_process_start_time(identities["client"]) is None
        assert not list(directory.iterdir())
    finally:
        if receipt.exists() and daemon is None:
            daemon = json.loads(receipt.read_text())["daemon"]
            started = get_process_start_time(daemon)
        if started is not None:
            terminate_pid(daemon, force=True, expected_start_time=started)
