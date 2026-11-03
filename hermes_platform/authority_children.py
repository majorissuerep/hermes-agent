"""Bounded native children supervised by their exact process identities."""
from __future__ import annotations

import contextlib
import os
import queue
import subprocess
import threading
import time

import psutil


class NativeChild:
    """Stream bounded output without a plaintext spool or an inherited caller FD."""

    def __init__(self, command, *, env, cwd, key_fd, input_bytes=b"", deadline=3600):
        from agent.memory_provider import spawn_context_thread
        self.output = queue.Queue(maxsize=128)
        self.cancelled = threading.Event()
        self.finished = threading.Event()
        self.last_poll = time.monotonic()
        self.deadline = self.last_poll + deadline
        self.process = subprocess.Popen(command, env=env, cwd=cwd, pass_fds=(key_fd,),
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        start_new_session=True)
        try:
            self.identity = psutil.Process(self.process.pid)
            self.started = self.identity.create_time()
            self.descendants = {}
            self.returncode = None
            self.reason = None
            self.threads = [spawn_context_thread(self._read, args=(name, stream), name="hermes-native-" + name, daemon=True)
                            for name, stream in (("stdout", self.process.stdout), ("stderr", self.process.stderr))]
            self.threads.append(spawn_context_thread(self._input, args=(input_bytes,), name="hermes-native-stdin", daemon=True))
            for thread in self.threads:
                thread.start()
            self.monitor = spawn_context_thread(self._watch, name="hermes-native-child", daemon=True)
            self.monitor.start()
        except BaseException:
            # Popen still owns its unreaped direct child: its PID cannot be reused.
            if hasattr(self, "descendants"):
                self._terminate()
            self.process.kill()
            self.process.communicate(timeout=5)
            raise

    def _input(self, data):
        try:
            self.process.stdin.write(data)
            self.process.stdin.close()
        except (BrokenPipeError, OSError):
            return

    def _read(self, name, stream):
        try:
            while chunk := os.read(stream.fileno(), 16384):
                while not self.cancelled.is_set():
                    try:
                        self.output.put((name, chunk), timeout=0.2)
                        break
                    except queue.Full:
                        continue
        except OSError:
            return
        finally:
            stream.close()

    def _remember_children(self):
        try:
            if self.identity.is_running() and self.identity.create_time() == self.started:
                for child in self.identity.children(recursive=True):
                    self.descendants.setdefault((child.pid, child.create_time()), child)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return

    def _terminate(self):
        self._remember_children()
        targets = [*reversed(list(self.descendants.values())), self.identity]
        for target in targets:
            try:
                # psutil retains creation time and refuses a reused PID.
                if target.is_running():
                    target.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _watch(self):
        try:
            while self.process.poll() is None:
                self._remember_children()
                now = time.monotonic()
                if now >= self.deadline or now - self.last_poll >= 20 or self.cancelled.is_set():
                    self.reason = self.reason or ("deadline" if now >= self.deadline else "client_closed")
                    self.cancelled.set()
                    self._terminate()
                    break
                self.finished.wait(0.1)
            self.returncode = self.process.wait(timeout=5)
            self._terminate()  # a native child may not leave its own children behind
            for thread in self.threads:
                thread.join(timeout=2)
        finally:
            self.finished.set()

    def poll(self):
        self.last_poll = time.monotonic()
        chunks = []
        for _ in range(16):
            try:
                chunks.append(self.output.get_nowait())
            except queue.Empty:
                break
        done = self.finished.is_set() and self.output.empty() and not any(thread.is_alive() for thread in self.threads)
        return chunks, done, self.returncode, self.reason

    def close(self):
        self.reason = "cancelled"
        self.cancelled.set()
        self._terminate()
        self.monitor.join(timeout=10)
        with contextlib.suppress(OSError):
            self.process.stdin.close()
