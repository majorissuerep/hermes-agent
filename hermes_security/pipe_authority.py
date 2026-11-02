"""Ephemeral kernel-pipe transport for local executors with networking disabled.

The directory contains only FIFO nodes and an empty lock inode. Requests and keys
exist in process memory and kernel buffers; no credential bytes are written to a
regular file. The owning authority still authenticates the exact home/session.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import select
import stat
import tempfile
import threading
import time

LIMIT = 4096
TIMEOUT = 5.0


class PipeServer:
    def __init__(self, respond):
        from hermes_constants import socket_safe_tmpdir
        self.directory = tempfile.TemporaryDirectory(prefix="hermes-pipe-", dir=socket_safe_tmpdir())
        self.path = Path(self.directory.name)
        self.respond = respond
        self.stopped, self.done = threading.Event(), threading.Event()
        try:
            for name in ("request", "response"):
                os.mkfifo(self.path / name, 0o600)
            (self.path / "lock").touch(mode=0o600)
            self.fd = os.open(self.path / "request", os.O_RDWR | os.O_NONBLOCK)
        except BaseException:
            self.directory.cleanup()
            raise

    def serve_forever(self, poll_interval=0.05):
        buffer = b""
        try:
            while not self.stopped.is_set():
                if not select.select([self.fd], [], [], poll_interval)[0]:
                    continue
                buffer += os.read(self.fd, LIMIT)
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if len(line) <= LIMIT:
                        self._reply(line)
                if len(buffer) > LIMIT:
                    buffer = b""
        finally:
            self.done.set()

    def _reply(self, line):
        try:
            request = json.loads(line)
        except (ValueError, UnicodeError):
            return
        if not isinstance(request, dict) or not isinstance(request.get("request_id"), str):
            return
        response = {**self.respond(request), "request_id": request["request_id"]}
        payload = json.dumps(response).encode("utf-8") + b"\n"
        if len(payload) > LIMIT:
            return
        try:
            fd = os.open(self.path / "response", os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            try:
                os.write(fd, payload)
            finally:
                os.close(fd)
        except (BrokenPipeError, BlockingIOError, FileNotFoundError):
            return  # a cancelled client must not strand the serving thread
        except OSError as exc:
            import errno
            if exc.errno != errno.ENXIO:
                raise

    def shutdown(self):
        self.stopped.set()
        self.done.wait(TIMEOUT + 1)

    def server_close(self):
        os.close(self.fd)
        self.directory.cleanup()


def _open(directory: Path, name: str, kind: int) -> int:
    fd = os.open(directory / name, os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if (stat.S_IFMT(info.st_mode) != kind or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600):
        os.close(fd)
        raise ValueError("unprotected authority pipe")
    return fd


def redeem(grant: dict) -> dict:
    """Serialize clients through a never-unlinked lock and correlate every response."""
    from contextlib import ExitStack
    from pm.filesystem import lock_fd

    raw = grant["pipe"]
    if not isinstance(raw, str) or not Path(raw).is_absolute() or os.name != "posix":
        raise ValueError("invalid authority pipe")
    directory = Path(raw)
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError("unprotected authority directory")
    with ExitStack() as resources:
        lock = _open(directory, "lock", stat.S_IFREG)
        resources.callback(os.close, lock)
        if not lock_fd(lock, wait=True, timeout=TIMEOUT):
            raise TimeoutError("authority pipe is busy")
        reader = _open(directory, "response", stat.S_IFIFO)
        resources.callback(os.close, reader)
        writer = _open(directory, "request", stat.S_IFIFO)
        resources.callback(os.close, writer)
        request_id = secrets.token_hex(16)
        payload = json.dumps({k: grant[k] for k in ("token", "home", "session_id")} | {
            "request_id": request_id}).encode("utf-8") + b"\n"
        if len(payload) > LIMIT:
            raise ValueError("authority request is too large")
        os.write(writer, payload)
        return _read_response(reader, request_id)


def _read_response(reader: int, request_id: str) -> dict:
    deadline, buffer = time.monotonic() + TIMEOUT, b""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([reader], [], [], max(0, remaining))[0]:
            raise TimeoutError("authority response unavailable")
        buffer += os.read(reader, LIMIT)
        if len(buffer) > LIMIT:
            raise ValueError("authority response is too large")
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            response = json.loads(line)
            if isinstance(response, dict) and response.get("request_id") == request_id:
                return response
