"""Private stdio runtime driver for native security E2E contracts."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from tests.e2e.core.security._helpers import hermetic_env, kill_group
from tests.e2e.vault_home import spawn_unlocked


class NativeRuntime:
    """The real stdio backend the local Ink TUI drives (``python -m tui_runtime``)."""

    def __init__(self, home: Path, log_path: Path, extra_env: dict[str, str] | None = None) -> None:
        import queue
        import threading

        self._log = open(log_path, "a", encoding="utf-8")
        self.proc = spawn_unlocked(
            [sys.executable, "-m", "tui_runtime"], cwd=str(home), env=hermetic_env(home, extra_env),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log, text=True, bufsize=1,
            start_new_session=True,
        )
        self._q: queue.Queue[dict[str, Any]] = queue.Queue()
        self._rid = 0
        self.seen: list[dict[str, Any]] = []
        threading.Thread(target=self._read, daemon=True, name="native-security-stdio-reader").start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            try:
                self._q.put(json.loads(line))
            except json.JSONDecodeError:
                continue

    def wait(self, pred: Callable[[dict[str, Any]], bool], timeout: float = 90.0) -> dict[str, Any]:
        import queue

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None and self._q.empty():
                break
            try:
                msg = self._q.get(timeout=0.25)
            except queue.Empty:
                continue
            self.seen.append(msg)
            if pred(msg):
                return msg
        tail = [(m.get("method"), (m.get("params") or {}).get("type"), m.get("id")) for m in self.seen[-20:]]
        raise AssertionError(f"tui_runtime: condition not met within {timeout}s (rc={self.proc.poll()}); last {tail}")

    def call(self, method: str, params: dict[str, Any], timeout: float = 90.0) -> dict[str, Any]:
        self._rid += 1
        rid = self._rid
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        return self.wait(lambda m: m.get("id") == rid and "method" not in m, timeout)

    def ok(self, method: str, params: dict[str, Any], timeout: float = 90.0) -> dict[str, Any]:
        reply = self.call(method, params, timeout)
        assert "result" in reply, f"{method} failed: {reply.get('error')}"
        return reply["result"]

    def turn(self, sid: str, text: str, timeout: float = 90.0) -> dict[str, Any]:
        """Submit one prompt and wait for that session's terminal turn event."""
        self.ok("prompt.submit", {"session_id": sid, "text": text})

        def done(m: dict[str, Any]) -> bool:
            p = m.get("params") or {}
            return (m.get("method") == "event" and p.get("session_id") == sid
                    and p.get("type") in {"message.complete", "error"})
        ev = self.wait(done, timeout)
        assert ev["params"]["type"] == "message.complete", f"turn failed: {ev}"
        return ev

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=60)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        kill_group(self.proc)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        self._log.close()
