"""Process identity guards use live, test-owned child processes on the native host."""

import os
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


def test_retained_state_cleanup_requires_known_death_of_exact_child_identity():
    import subprocess
    import sys
    from hermes_platform.processes import get_process_start_time, process_record_is_dead

    assert not process_record_is_dead({'pid':os.getpid(), 'start_time':None})
    assert not process_record_is_dead({'pid':-1, 'start_time':1})
    with subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read(1)'],
                          stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as child:
        try:
            started = get_process_start_time(child.pid)
            assert started is not None
            record = {'pid':child.pid, 'start_time':started}
            assert not process_record_is_dead(record)
            child.communicate(b'x', timeout=10)
            assert process_record_is_dead(record)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
