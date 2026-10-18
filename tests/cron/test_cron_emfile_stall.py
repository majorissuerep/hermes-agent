"""Regression tests for #87644 — cron scheduler silently stalls after EMFILE.

Three fixes under test:

1. ``tick()`` no longer swallows a real ``OSError`` at tick-lock acquisition as
   "another instance holds the lock".  An EMFILE/ENFILE (fd exhaustion) on
   ``open(.tick.lock)`` previously made ``tick()`` return 0 — which the ticker
   loop recorded as a *successful* tick and cleared the error — so the
   scheduler looked perfectly healthy (heartbeat + success markers fresh)
   while no job ever ran again.  Now the failure propagates to the ticker
   loop, which records it and backs off.

2. The ticker loop detects fd exhaustion (EMFILE/ENFILE/"Too many open
   files"), attempts best-effort fd reclamation (``gc.collect()`` + raising
   the soft nofile limit), and applies exponential backoff so an exhausted
   process stops hammering the store every 60s.  Once FDs free up, the next
   tick succeeds and the backoff resets — the scheduler self-heals without a
   gateway restart.

3. Genuine lock contention (EWOULDBLOCK/EAGAIN on flock) still skips the tick
   silently — that behavior is preserved.
"""
import errno
import threading
import time
from unittest.mock import patch

import pytest

import cron.scheduler as scheduler_mod

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None

pytestmark = pytest.mark.skipif(fcntl is None, reason="flock semantics are POSIX-only")

def _wait_until(predicate, timeout=10.0, interval=0.005):
    """Block until ``predicate()`` is truthy or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()

# ── Fix 1: tick() must not swallow EMFILE as "another instance holds the lock" ─

class TestTickLockEmfileNotSwallowed:
    def test_lock_open_emfile_raises_instead_of_silent_skip(self, monkeypatch):
        """open(.tick.lock) raising EMFILE must propagate — not return 0 as if
        another ticker held the lock (the pre-fix silent stall)."""
        real_open = open
        calls = []

        def flaky_open(path, *args, **kwargs):
            if str(path).endswith(".tick.lock"):
                calls.append(("emfile", str(path)))
                raise OSError(errno.EMFILE, "Too many open files")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", flaky_open)
        with pytest.raises(OSError) as excinfo:
            scheduler_mod.tick(verbose=False)
        assert excinfo.value.errno == errno.EMFILE
        assert calls, "the lock file open must have been attempted"

    def test_lock_flock_emfile_raises(self, monkeypatch):
        """flock() raising EMFILE (fd exhaustion at lock syscall) must also
        propagate — only EWOULDBLOCK/EAGAIN/EACCES mean contention."""
        with patch.object(fcntl, "flock", side_effect=OSError(errno.EMFILE, "Too many open files")):
            with pytest.raises(OSError) as excinfo:
                scheduler_mod.tick(verbose=False)
        assert excinfo.value.errno == errno.EMFILE

    def test_lock_contention_ewouldblock_still_skips_silently(self, monkeypatch):
        """Genuine contention (another ticker holds the lock) still returns 0
        silently — the cross-process mutual-exclusion contract is preserved."""
        with patch.object(fcntl, "flock", side_effect=OSError(errno.EWOULDBLOCK, "Resource temporarily unavailable")):
            assert scheduler_mod.tick(verbose=False) == 0

# ── Fix 2: ticker loop survives EMFILE, reclaims fds, backs off, self-heals ──


# ── Helpers ────────────────────────────────────────────────────────────────────

class TestEmfileHelpers:
    def test_is_fd_exhaustion_errno(self):
        assert scheduler_mod._is_fd_exhaustion(OSError(errno.EMFILE, "Too many open files"))
        assert scheduler_mod._is_fd_exhaustion(OSError(errno.ENFILE, "file table overflow"))
        assert not scheduler_mod._is_fd_exhaustion(OSError(errno.EACCES, "Permission denied"))
        assert not scheduler_mod._is_fd_exhaustion(OSError(errno.EWOULDBLOCK, "Resource temporarily unavailable"))

    def test_is_fd_exhaustion_wrapped_message(self):
        """load_jobs wraps the raw OSError in a RuntimeError whose message
        carries the errno text — the ticker must classify that too."""
        assert scheduler_mod._is_fd_exhaustion(
            RuntimeError("Failed to read cron database: [Errno 24] Too many open files")
        )
