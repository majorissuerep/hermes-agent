"""Local process identity and advisory locks, independent of any Hermes interface."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
import signal
from typing import Any

import psutil


START_TIME_DRIFT_TOLERANCE = 200


def pid_exists(pid: int) -> bool:
    """Treat zombies as dead without signalling a possibly unrelated process."""
    try:
        process = psutil.Process(int(pid))
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return psutil.pid_exists(int(pid))


def get_process_start_time(pid: int) -> int | None:
    """Linux boot ticks or macOS creation centiseconds, matching saved local identities."""
    from hermes_platform.host.facts import os_family

    if os_family() == "linux":
        try:
            # The comm field may contain spaces or parentheses; field 22 is offset 19
            # after its closing parenthesis, not the 22nd whitespace-delimited token.
            fields = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
            return int(fields[19])
        except (OSError, IndexError, ValueError):
            return None
    try:
        return round(psutil.Process(int(pid)).create_time() * 100)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def start_time_fingerprints_match(
    recorded: Any, current: Any, tolerance: int = START_TIME_DRIFT_TOLERANCE,
) -> bool:
    """Allow small same-host clock adjustments during liveness reconciliation."""
    return abs(int(current) - int(recorded)) <= tolerance


def process_record_is_live(record: Any) -> bool:
    """Check a saved local PID, including its start fingerprint when one was saved."""
    if not isinstance(record, dict):
        return False
    try:
        pid = int(record.get("pid", 0))
        if pid <= 0 or not pid_exists(pid):
            return False
        recorded = record.get("start_time")
        if recorded is None:
            return True
        current = get_process_start_time(pid)
        return current is not None and process_start_times_agree(current, recorded)
    except (TypeError, ValueError, OverflowError):
        return False


def process_start_times_agree(current: Any, *recorded: Any) -> bool:
    """Strict identity guard for destructive operations; malformed inputs raise."""
    current = float(current)
    return current > 0 and all(value > 0 and abs(value - current) <= .001
                               for value in map(float, recorded))


def terminate_pid(
    pid: int, *, force: bool = False, expected_start_time: float | None = None,
) -> None:
    """Signal a local process, refusing a provided stale identity before either signal."""
    if expected_start_time is not None:
        current = get_process_start_time(pid)
        if current is None or not process_start_times_agree(current, expected_start_time):
            raise OSError(f"refusing to terminate PID {pid}; process identity changed")
    if force and expected_start_time is None:
        raise OSError(f"refusing to force-kill PID {pid} without a process start-time guard")
    os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)


def try_acquire_file_lock(handle, *, shared: bool = False) -> bool:
    """Nonblocking POSIX advisory lock for the supported Linux/macOS runtimes."""
    import fcntl

    try:
        fcntl.flock(handle.fileno(), (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def release_file_lock(handle) -> None:
    import fcntl

    with contextlib.suppress(OSError):
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
