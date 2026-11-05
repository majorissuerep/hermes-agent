"""Memory-only output from a client whose daemon may inherit its standard streams.

The client is our unreaped Popen child, so its timeout signal cannot target a
reused PID. Its descendants have separate session ownership: their inherited
pipes must neither delay the result until EOF nor be killed at client exit.
Callers retain responsibility for their daemon's normal and timeout cleanup.
"""
from __future__ import annotations

import os
import selectors
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, BinaryIO


class ClientOutputLimit(RuntimeError):
    """The client exceeded the bounded in-memory output budget."""


@dataclass
class _Streams:
    selector: Any
    limit: int
    pending: memoryview
    input_source: BinaryIO | None = None
    output_sink: BinaryIO | None = None
    output: dict[str, bytearray] = field(default_factory=lambda: {
        "stdout": bytearray(), "stderr": bytearray()})
    total: int = 0

    def close(self, handle) -> None:
        self.selector.unregister(handle)
        handle.close()

    def ready(self, key) -> None:
        handle, channel = key.fileobj, key.data
        try:
            if channel == "stdin":
                self.pending = self.pending[os.write(handle.fileno(), self.pending[:65536]):]
                if not self.pending and self.input_source is not None:
                    self.pending = memoryview(self.input_source.read(65536))
                if not self.pending:
                    self.close(handle)
                return
            chunk = os.read(handle.fileno(), 65536)
        except (BlockingIOError, InterruptedError):
            return
        except BrokenPipeError:
            self.close(handle)
            return
        if not chunk:
            self.close(handle)
            return
        self.total += len(chunk)
        if self.total > self.limit:
            raise ClientOutputLimit(f"The client exceeded its {self.limit} byte output limit")
        if channel == "stdout" and self.output_sink is not None:
            if self.output_sink.write(chunk) != len(chunk):
                raise OSError("The client output sink did not accept the complete chunk")
        else:
            self.output[channel].extend(chunk)


def _register(selector, child, payload: bytes, keep_input_open: bool = False) -> None:
    for channel in ("stdout", "stderr", "stdin"):
        handle = getattr(child, channel)
        if handle is None:
            continue
        if channel == "stdin" and not payload:
            if not keep_input_open:
                handle.close()
            continue
        os.set_blocking(handle.fileno(), False)
        selector.register(handle, selectors.EVENT_WRITE if channel == "stdin" else selectors.EVENT_READ,
                          channel)


def _collect(child, streams: _Streams, timeout: float, check_alive=None) -> int | None:
    deadline = time.monotonic() + timeout
    expired = False
    while child.poll() is None:
        if check_alive is not None:
            check_alive()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            child.kill()
            child.wait()
            expired = True
            break
        for key, _ in streams.selector.select(min(remaining, 0.05)):
            streams.ready(key)
    # Read currently available data; a daemon still holding a pipe cannot hold
    # the call open. Output emitted after the client's exit belongs to the daemon.
    while ready := streams.selector.select(0):
        for key, _ in ready:
            if key.data == "stdin":
                streams.close(key.fileobj)
            else:
                streams.ready(key)
    return None if expired else child.wait()


def run_client(argv, *, env, payload: bytes | None = None, timeout: float,
               max_output: int = 64 * 1024 * 1024, input_source: BinaryIO | None = None,
               output_sink: BinaryIO | None = None, check_alive=None,
               keep_input_open: bool = False) -> tuple[int | None, bytes, bytes]:
    """Return at this client's exit, using bounded pipes on Linux and macOS.

    ``None`` is the timeout result; output overflow raises without returning
    partial JSON as success. No pathname, disk spool or pipe-EOF wait is used.
    A caller-owned input source replaces ``payload``; a caller-owned stdout sink avoids
    buffering transferred profile trees. The caller proves/stages its files in protected RAM;
    streams remain open, stderr is still captured, and the total output bound still applies.
    Fixed remote transfers may hold an otherwise empty stdin pipe as their owner
    lifetime. It carries no data and closes on cancellation, failure or owner exit.
    """
    if timeout <= 0 or max_output <= 0:
        raise ValueError("Client capture needs positive timeout and output bounds")
    if input_source is not None and payload is not None:
        raise ValueError("Client capture accepts one input source")
    if keep_input_open and (payload is not None or input_source is not None):
        raise ValueError("The fixed lifetime pipe cannot also carry an input payload")
    if check_alive is not None:
        check_alive()
    data = input_source.read(65536) if input_source is not None else payload or b""
    child = subprocess.Popen(
        argv, stdin=subprocess.PIPE if payload is not None or input_source is not None or keep_input_open else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, close_fds=True)
    try:
        with selectors.DefaultSelector() as selector:
            _register(selector, child, data, keep_input_open)
            streams = _Streams(selector, max_output, memoryview(data), input_source, output_sink)
            code = _collect(child, streams, timeout, check_alive)
            return code, bytes(streams.output["stdout"]), bytes(streams.output["stderr"])
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        for handle in (child.stdin, child.stdout, child.stderr):
            if handle is not None:
                handle.close()
