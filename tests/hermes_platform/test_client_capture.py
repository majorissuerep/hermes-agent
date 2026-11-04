"""Real POSIX client pipes: bounded duplex I/O and independent daemon lifetime."""
from __future__ import annotations

import os
import socket
import sys

import pytest

from hermes_platform.client_capture import ClientOutputLimit, run_client
from hermes_platform.processes import get_process_start_time, pid_exists
from tests.waiting import wait_until


@pytest.mark.platforms("posix")
def test_client_exit_returns_while_its_owned_daemon_still_holds_stdout():
    script = (
        "import os,socket,sys\n"
        "pid=os.fork()\n"
        "if pid==0:\n"
        " with socket.create_connection(('127.0.0.1',int(sys.argv[1]))) as control:\n"
        "  control.recv(1)\n"
        " os._exit(0)\n"
        "print(pid,file=sys.stderr,flush=True)\n"
        "print('complete',flush=True)\n"
        "os._exit(3)\n"
    )
    # The orphan is no longer in pytest's process subtree after its client exits.
    # Let exactly this synthetic daemon exit itself, without bypassing the guard.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        code, output, error = run_client(
            [sys.executable, "-c", script, str(listener.getsockname()[1])], env=os.environ.copy(), timeout=5)
        daemon = int(error)
        try:
            assert (code, output) == (3, b"complete\n")
            assert get_process_start_time(daemon) is not None
        finally:
            connection, _ = listener.accept()
            with connection:
                connection.sendall(b"stop")
            assert wait_until(lambda: not pid_exists(daemon), timeout=5)


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
