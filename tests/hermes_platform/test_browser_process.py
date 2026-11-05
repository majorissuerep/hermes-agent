"""Native daemon identity is exact; an action argument cannot masquerade as a close command."""
import os
import json
from pathlib import Path
import subprocess
from selectors import DefaultSelector, EVENT_READ
import sys

import pytest

from hermes_platform.browser_process import parse_agent_browser_command, browser_process_record


def _record_absence_diagnostics(pid, socket):
    """Only controlled-child facts, never environment values, leave a failed native assertion."""
    import psutil
    from hermes_platform.processes import get_process_start_time

    result = {}
    try:
        process = psutil.Process(pid)
        result['expected_executable_matches'] = Path(process.exe()).resolve() == Path(sys.executable).resolve()
        result['effective_uid_matches'] = process.uids().effective == os.geteuid()
        result['socket_environment_matches'] = process.environ().get('AGENT_BROWSER_SOCKET_DIR') == str(socket)
        before, after = get_process_start_time(pid), get_process_start_time(pid)
        result['native_start_identity_present'] = before is not None
        result['native_start_identity_stable'] = before == after
        result['zombie'] = process.status() == psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ValueError) as exc:
        result['error_type'] = type(exc).__name__
    print('HERMES_NATIVE_PID_DIAGNOSTIC ' + json.dumps(result, sort_keys=True))


def test_browser_action_uses_the_actual_command_position():
    command = parse_agent_browser_command(['/pm/agent-browser', '--session', 'a', '--engine', 'chrome', '--json', 'close'])
    assert command.action == 'close' and command.prefix[-1] == '--json'
    command = parse_agent_browser_command(['/pm/agent-browser', '--cdp', 'http://127.0.0.1:1', 'eval', 'close'])
    assert command.action == 'eval' and command.arguments == ('close',)
    assert parse_agent_browser_command(['/pm/agent-browser', '--unknown', 'close']) is None
    assert parse_agent_browser_command(['/pm/agent-browser', '--session']) is None


@pytest.mark.platforms('linux', 'macos')
def test_native_process_requires_exact_executable_and_socket_identity(tmp_path):
    socket = tmp_path/'socket'
    socket.mkdir()
    env = {**os.environ, 'AGENT_BROWSER_SOCKET_DIR':str(socket)}
    with subprocess.Popen([sys.executable, '-c',
                           'import sys; print("READY", flush=True); sys.stdin.read(1)'], env=env,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as child:
        try:
            # Popen is not the child's application-readiness signal. Inspect the
            # positive identity only after this inert child has entered Python.
            with DefaultSelector() as ready:
                ready.register(child.stdout, EVENT_READ)
                assert ready.select(timeout=10), "bounded native helper readiness"
            assert child.stdout.readline() == b"READY\n"
            record = browser_process_record(child.pid, executable=sys.executable, socket_dir=socket)
            if record is None:
                _record_absence_diagnostics(child.pid, socket)
            assert record and record['pid'] == child.pid
            assert browser_process_record(child.pid, executable=tmp_path/'same-name-other-binary', socket_dir=socket) is None
            assert browser_process_record(child.pid, executable=sys.executable, socket_dir=tmp_path/'other-socket') is None
            assert browser_process_record(None, executable=sys.executable, socket_dir=socket) is None
            assert child.poll() is None
            child.communicate(b'x', timeout=10)
            assert browser_process_record(child.pid, executable=sys.executable, socket_dir=socket) is None
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)


@pytest.mark.platforms('linux', 'macos')
def test_harness_absence_preserves_live_pid_and_unreadable_pid_sources(tmp_path):
    from hermes_platform.browser_process import harness_runtime_absent

    runtime = tmp_path/'runtime'; runtime.mkdir(mode=0o700)
    pid_file = runtime/'bu.pid'
    with subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read(1)'],
                          stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as child:
        try:
            pid_file.write_text(str(child.pid))
            assert not harness_runtime_absent(pid_file, runtime=runtime, name='default')
            child.communicate(b'x', timeout=10)
            assert harness_runtime_absent(pid_file, runtime=runtime, name='default')
            retained = runtime/'retained-pid'; pid_file.rename(retained)
            pid_file.symlink_to(retained)
            assert not harness_runtime_absent(pid_file, runtime=runtime, name='default')
            assert retained.read_text() == str(child.pid)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
