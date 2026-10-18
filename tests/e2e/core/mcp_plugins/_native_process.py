"""Native CLI/stdio process fixtures for outgoing MCP conformance.

No hosted gateway, socket listener or installed user state is involved.
"""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from tests.waiting import wait_until as wait_for

READY_TIMEOUT = 120.0
TURN_TIMEOUT = 240.0


def wait_until(pred, timeout, what, interval=0.05):
    return wait_for(pred, timeout=timeout, what=what, interval=interval)

def tagged_pids(tag: str, var: str = "PARITY_TREE_TAG") -> set[int]:
    """Live PIDs whose environment carries ``<var>=<tag>`` (reparented orphans included)."""
    needle = f"{var}={tag}".encode()
    found: set[int] = set()
    me = os.getpid()
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == me:
            continue
        try:
            with open(f"/proc/{entry}/environ", "rb") as fh:
                env = fh.read()
            with open(f"/proc/{entry}/stat", "rb") as fh:
                state = fh.read().rsplit(b")", 1)[1].split()[0]
        except OSError:
            continue
        if state != b"Z" and needle in env.split(b"\0"):
            found.add(int(entry))
    return found


def hermes_argv(*args: str) -> list[str]:
    return [sys.executable, "-m", "hermes_cli.main", *args]


def terminate(proc: subprocess.Popen, timeout: float = 30.0) -> int | None:
    """Graceful SIGTERM (the normal stop), SIGKILL only if it overstays."""
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
    return proc.returncode


class RpcClient:
    """Minimal JSON-RPC 2.0 client over a line/frame transport (``send`` + ``recv`` callables)."""

    def __init__(self, send: Callable[[str], None], frames: queue.Queue[str | None]) -> None:
        self._send = send
        self._frames = frames
        self._next_id = 0
        self.events: list[dict[str, Any]] = []
        self._responses: dict[Any, dict[str, Any]] = {}

    def _pump(self, timeout: float) -> bool:
        try:
            raw = self._frames.get(timeout=timeout)
        except queue.Empty:
            return False
        if raw is None:
            raise AssertionError("JSON-RPC transport closed")
        msg = json.loads(raw)
        if msg.get("method") == "event":
            self.events.append(msg.get("params") or {})
        elif "id" in msg and "method" in msg:
            # Server→client request (approval/clarify/...): this scripted turn never needs one.
            self._send(json.dumps({"jsonrpc": "2.0", "id": msg["id"],
                                   "error": {"code": -32601, "message": "native fixture: unsupported"}}))
        elif "id" in msg:
            self._responses[msg["id"]] = msg
        return True

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 60.0) -> Any:
        self._next_id += 1
        rid = f"native-{self._next_id}"
        self._send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while rid not in self._responses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"{method}: no response within {timeout}s")
            self._pump(min(remaining, 0.5))
        resp = self._responses.pop(rid)
        if "error" in resp:
            raise AssertionError(f"{method} failed: {resp['error']}")
        return resp.get("result")

    def wait_event(self, etype: str, pred: Callable[[dict], bool] = lambda _e: True,
                   timeout: float = TURN_TIMEOUT) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        seen = 0
        while True:
            for ev in self.events[seen:]:
                if ev.get("type") == etype and pred(ev):
                    return ev
            seen = len(self.events)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"no {etype!r} event within {timeout}s; saw {[e.get('type') for e in self.events][-30:]}")
            self._pump(min(remaining, 0.5))


@dataclass
class StreamCapture:
    """Every stdout line (in order) plus the stderr text of a child, pumped on threads."""

    stdout_lines: queue.Queue[str | None] = field(default_factory=queue.Queue)
    stdout_seen: list[str] = field(default_factory=list)
    stderr_chunks: list[str] = field(default_factory=list)

    def start(self, proc: subprocess.Popen) -> StreamCapture:
        def out() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                self.stdout_seen.append(line)
                self.stdout_lines.put(line)
            self.stdout_lines.put(None)

        def err() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                self.stderr_chunks.append(line)

        threading.Thread(target=out, daemon=True, name="native-stdout").start()
        threading.Thread(target=err, daemon=True, name="native-stderr").start()
        return self

    @property
    def stderr(self) -> str:
        return "".join(self.stderr_chunks)
