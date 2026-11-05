"""Native daemon identity is exact; an action argument cannot masquerade as a close command."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hermes_platform.browser_process import parse_agent_browser_command, browser_process_record


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
    with subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read(1)'], env=env,
                          stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as child:
        try:
            record = browser_process_record(child.pid, executable=sys.executable, socket_dir=socket)
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
