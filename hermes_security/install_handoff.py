"""Private descriptor broker for the trusted source installer's sequential stages.

The inherited socket is distinct from ordinary operation grants. Each application
bootstrap asks for a fresh one-shot key FD, consumes it, and closes the broker FD
before launching PM workers or model-controlled tools. No key or socket path is
stored in the environment or on disk.
"""
from __future__ import annotations

import array
import contextlib
from contextlib import contextmanager
import os
from pathlib import Path
import select
import socket
import threading

ENV_NAME = "__HERMES_INSTALL_KEYS_FD"


def _serve(server, home, session_id, stop):
    from agent.session_context import scoped_current_session_id
    from hermes_security.handoff import prepare_child_key

    with scoped_current_session_id(session_id):
        while not stop.is_set():
            if not select.select([server], [], [], 0.1)[0]:
                continue
            if server.recv(16) != b"key":
                continue
            handoff = None
            try:
                handoff = prepare_child_key(home)
                if handoff is None:
                    raise RuntimeError("installer owner is locked")
                server.sendmsg([b"key"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                          array.array("i", [handoff[0]]))])
            except (OSError, RuntimeError, ValueError):
                with contextlib.suppress(OSError):
                    server.send(b"unavailable")
            finally:
                if handoff is not None:
                    os.close(handoff[0])


@contextmanager
def installer_keys(home: Path, session_id: str, env: dict):
    """Only the explicitly trusted installation tree inherits this socket."""
    from agent.memory_provider import spawn_context_thread

    server, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.settimeout(1)
    stop = threading.Event()
    thread = spawn_context_thread(_serve, args=(server, home, session_id, stop),
                                  name="source-install-keys", daemon=True)
    env[ENV_NAME] = str(child.fileno())
    thread.start()
    try:
        yield child.fileno()
    finally:
        env.pop(ENV_NAME, None)
        stop.set()
        thread.join(timeout=6)
        child.close()
        server.close()
        if thread.is_alive():
            raise TimeoutError("installer key handoff did not stop")


def adopt_install_key() -> None:
    """Receive one fresh sealed descriptor; never promote an operation grant."""
    raw = os.environ.pop(ENV_NAME, "")
    if not raw:
        return
    if not raw.isdigit() or int(raw) < 3:
        raise ValueError("invalid private installer descriptor")
    received = array.array("i")
    with socket.socket(fileno=int(raw)) as channel:
        channel.settimeout(5)
        channel.send(b"key")
        data, ancillary, flags, _ = channel.recvmsg(32, socket.CMSG_SPACE(received.itemsize),
                                                  getattr(socket, "MSG_CMSG_CLOEXEC", 0))
        for level, kind, value in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                received.frombytes(value[:len(value) - len(value) % received.itemsize])
    if data != b"key" or flags & socket.MSG_CTRUNC or len(received) != 1:
        for fd in received:
            os.close(fd)
        raise RuntimeError("the private installer key handoff is unavailable")
    os.environ["HERMES_VAULT_KEY_FD"] = str(received[0])
