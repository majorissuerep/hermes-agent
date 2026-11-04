"""Real POSIX client pipes: bounded duplex I/O and independent daemon lifetime."""
from __future__ import annotations

import os
import sys

import pytest

from hermes_platform.client_capture import ClientOutputLimit, run_client
from hermes_platform.processes import get_process_start_time, terminate_pid


@pytest.mark.platforms("posix")
def test_client_exit_returns_while_its_owned_daemon_still_holds_stdout():
    script = (
        "import os,signal,sys\n"
        "pid=os.fork()\n"
        "if pid==0:\n"
        " signal.pause()\n"
        " os._exit(0)\n"
        "print(pid,file=sys.stderr,flush=True)\n"
        "print('complete',flush=True)\n"
        "os._exit(3)\n"
    )
    code, output, error = run_client([sys.executable, "-c", script], env=os.environ.copy(), timeout=5)
    daemon = int(error)
    started = get_process_start_time(daemon)
    try:
        assert (code, output) == (3, b"complete\n")
        assert started is not None  # the daemon survived the client, without delaying its result
    finally:
        if started is not None:
            terminate_pid(daemon, force=True, expected_start_time=started)


@pytest.mark.platforms("posix")
def test_duplex_output_is_complete_and_overflow_refuses_partial_success():
    payload = b"private-input-" * 16384
    script = (
        "import sys\n"
        "sys.stderr.buffer.write(b'e'*131072); sys.stderr.flush()\n"
        "data=sys.stdin.buffer.read()\n"
        "sys.stdout.buffer.write(data[::-1]); sys.stdout.flush()\n"
    )
    code, output, error = run_client([sys.executable, "-c", script], env=os.environ.copy(),
                                     payload=payload, timeout=10)
    assert (code, output, error) == (0, payload[::-1], b"e" * 131072)
    with pytest.raises(ClientOutputLimit, match="output limit"):
        run_client([sys.executable, "-c", script], env=os.environ.copy(),
                   payload=payload, timeout=10, max_output=1024)
