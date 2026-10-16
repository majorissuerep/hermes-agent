"""An installer child can append diagnostics without writing plaintext state."""
from __future__ import annotations

from contextlib import contextmanager
import logging
import os
from pathlib import Path
import select
import threading

LOG_FD_ENV = "__HERMES_INSTALL_LOG_FD"
logger = logging.getLogger(__name__)


def _drain(read_fd: int, path: Path, stop: threading.Event, errors: list) -> None:
    from hermes_security.frames import append

    pending = b""
    try:
        with os.fdopen(read_fd, "rb", buffering=0) as reader:
            while True:
                if not select.select([reader], [], [], 0.1)[0]:
                    if stop.is_set():
                        break
                    continue
                chunk = os.read(reader.fileno(), 65536)
                if not chunk:
                    break
                pending += chunk
                complete = pending.rfind(b"\n") + 1
                if complete:
                    append(path, pending[:complete], purpose="log")
                    pending = pending[complete:]
            if pending:
                append(path, pending, purpose="log")
    except Exception as exc:
        logger.exception("Encrypted installer diagnostics could not be written")
        errors.append(exc)


@contextmanager
def encrypted_install_log(home: Path, env: dict[str, str]):
    from agent.memory_provider import spawn_context_thread

    read_fd, write_fd = os.pipe()
    env[LOG_FD_ENV] = str(write_fd)
    stop = threading.Event()
    errors = []
    thread = spawn_context_thread(_drain, args=(read_fd, home / "logs/install.log", stop, errors),
                                  name="encrypted-install-log", daemon=True)
    thread.start()
    try:
        yield (write_fd,)
    finally:
        os.close(write_fd)
        stop.set()
        thread.join(timeout=10)
        if thread.is_alive():
            raise TimeoutError("encrypted installer log did not finish flushing")
        if errors:
            raise errors[0]
