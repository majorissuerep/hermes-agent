"""Reap only owned sandbox descendants using kernel incarnation fingerprints."""

from __future__ import annotations

import ctypes
import os
import signal
import sys
import time
from pathlib import Path
from typing import Callable

Identity = tuple[int, int]  # (pid, kernel start time in clock ticks)
Finder = Callable[[], dict[Identity, str]]

REAP_TIMEOUT = 30.0
_PR_SET_CHILD_SUBREAPER = 36
_adopters = 0


def _set_subreaper(on: bool) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_CHILD_SUBREAPER, int(on), 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_CHILD_SUBREAPER) failed")


def adopt_orphans() -> None:
    """Start adopting orphaned descendants (refcounted; pair with ``release_orphans``)."""
    global _adopters
    if sys.platform.startswith("linux") and _adopters == 0:
        _set_subreaper(True)
    _adopters += 1


def release_orphans() -> None:
    """Stop adopting once no sandbox needs it. Processes already adopted stay our children."""
    global _adopters
    _adopters = max(0, _adopters - 1)
    if sys.platform.startswith("linux") and _adopters == 0:
        _set_subreaper(False)


def stat(pid: int) -> list[str] | None:
    """``/proc/<pid>/stat`` fields after ``comm`` (state is index 0, ppid 1, sid 3, start 19)."""
    try:
        return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace").rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return None


def start_time(pid: int) -> int | None:
    """Kernel start time of a live, non-zombie process; None when it is gone or a zombie."""
    fields = stat(pid)
    return int(fields[19]) if fields and fields[0] not in ("Z", "X") else None


def cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError:
        return "?"


def all_stats() -> dict[int, list[str]]:
    """Every live, non-zombie process."""
    out = {}
    for entry in os.listdir("/proc"):
        if entry.isdigit() and (fields := stat(int(entry))) is not None and fields[0] not in ("Z", "X"):
            out[int(entry)] = fields
    return out


def with_home(home: Path) -> Finder:
    """Every live process whose environment points HOME into ``home`` (the sandbox), except us."""
    needle = f"HOME={home}".encode()

    def find() -> dict[Identity, str]:
        found = {}
        for pid in all_stats():
            if pid == os.getpid():
                continue
            try:
                if needle not in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
                    continue
            except OSError:
                continue
            if (started := start_time(pid)) is not None:
                found[(pid, started)] = cmdline(pid)
        return found
    return find


def _collect_zombies(idents: dict[Identity, str]) -> None:
    """``waitpid`` the adopted ones that already exited, so no zombie outlives the test."""
    for pid, _ in idents:
        fields = stat(pid)
        if fields and fields[0] == "Z" and int(fields[1]) == os.getpid():
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass


def kill_identified(idents: dict[Identity, str]) -> list[str]:
    """SIGKILL exactly the recorded processes that are still the SAME process (pid and start time)."""
    killed = []
    for (pid, started), cmd in idents.items():
        if start_time(pid) != started:
            continue
        try:
            os.kill(pid, signal.SIGKILL)  # windows-footgun: ok — Linux-only suite, never reached on Windows
        except ProcessLookupError:
            continue
        killed.append(f"{pid}: {cmd[:160]}")
    return killed


def reap(*finders: Finder, timeout: float = REAP_TIMEOUT) -> list[str]:
    """Wait up to ``timeout`` s for every process the finders report to exit by itself, then kill
    the survivors (identity-checked). Returns the killed ones: an empty list means nothing leaked."""
    seen: dict[Identity, str] = {}
    deadline = time.monotonic() + timeout
    while True:
        for find in finders:
            seen.update(find())
        _collect_zombies(seen)
        live = {ident: cmd for ident, cmd in seen.items() if start_time(ident[0]) == ident[1]}
        if not live:
            return []
        if time.monotonic() >= deadline:
            break
        time.sleep(0.1)
    killed = kill_identified(live)
    settle = time.monotonic() + 10
    while time.monotonic() < settle and any(start_time(pid) == started for pid, started in live):
        _collect_zombies(live)
        time.sleep(0.05)
    _collect_zombies(live)
    return killed
