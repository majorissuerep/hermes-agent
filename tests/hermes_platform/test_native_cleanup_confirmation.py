"""Completion requires observed cleanup, not a monitor flag or a bounded join."""
import io
import queue
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from hermes_platform.authority_children import NativeChild


def inert_child(*, reaped=True, pipe_live=False, descendant_live=False):
    child = NativeChild.__new__(NativeChild)
    child._on_cleanup_complete = None
    child.output = queue.Queue()
    child.cancelled = threading.Event()
    child.finished = threading.Event()
    child.last_poll = time.monotonic()
    child.deadline = child.last_poll + 3600
    child.returncode = -9 if reaped else None
    child.reason = None
    child.process = SimpleNamespace(stdin=io.BytesIO(), poll=lambda: child.returncode)
    child.threads = [SimpleNamespace(is_alive=lambda: pipe_live, join=lambda timeout: None)]
    child.descendants = {1: SimpleNamespace(is_running=lambda: descendant_live)}
    child._remember_children = lambda: None
    child._terminate = lambda: None
    child.monitor = SimpleNamespace(join=lambda timeout: None, is_alive=lambda: False)
    return child


@pytest.mark.parametrize('reaped,pipe_live,descendant_live,complete', [
    (False, False, False, False), (True, True, False, False),
    (True, False, True, False), (True, False, False, True),
])
def test_stale_finished_flag_cannot_acknowledge_unreaped_or_open_cleanup(
        reaped, pipe_live, descendant_live, complete):
    child = inert_child(reaped=reaped, pipe_live=pipe_live, descendant_live=descendant_live)
    child.finished.set()
    assert child.poll()[1] is complete
    if complete:
        child.close()
    else:
        with pytest.raises(RuntimeError, match='cleanup remains pending'):
            child.close()


def test_monitor_retries_bounded_reap_failure_before_publishing_completion():
    child = inert_child(reaped=False)
    waits = []

    def wait(timeout):
        waits.append(timeout)
        if len(waits) == 1:
            assert not child.finished.is_set()
            raise subprocess.TimeoutExpired('inert child', timeout)
        assert not child.finished.is_set()
        child.returncode = -9
        return -9

    child.process.wait = wait
    child.cancelled.set()
    # No actual thread or timer: this is one failed wait and one confirmed reap.
    child.finished = SimpleNamespace(is_set=lambda: bool(completed),
                                     set=lambda: completed.append(True), wait=lambda timeout: None)
    completed = []
    child._watch()
    assert waits == [5, 5]
    assert child.finished.is_set() and child.poll()[1]
