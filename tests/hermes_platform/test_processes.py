"""Process identity guards use live, test-owned child processes on the native host."""

import subprocess
import sys

import pytest

from hermes_platform.processes import get_process_start_time, pid_exists, process_record_is_live, terminate_pid

pytestmark = pytest.mark.platforms("linux", "macos")


def test_stale_identity_cannot_terminate_a_live_process():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        started = get_process_start_time(child.pid)
        assert started is not None and pid_exists(child.pid)
        assert process_record_is_live({"pid": child.pid, "start_time": started})
        assert not process_record_is_live({"pid": child.pid, "start_time": started + 10_000})
        assert not process_record_is_live({"pid": child.pid, "start_time": "invalid"})
        with pytest.raises(OSError, match="identity changed"):
            terminate_pid(child.pid, expected_start_time=started + 10_000)
        assert child.poll() is None
        terminate_pid(child.pid, expected_start_time=started)
        assert child.wait(timeout=10) != 0
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)


def test_force_kill_requires_an_identity_even_for_a_test_owned_process():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        with pytest.raises(OSError, match="start-time guard"):
            terminate_pid(child.pid, force=True)
        assert child.poll() is None
        started = get_process_start_time(child.pid)
        assert started is not None
        terminate_pid(child.pid, force=True, expected_start_time=started)
        assert child.wait(timeout=10) != 0
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
