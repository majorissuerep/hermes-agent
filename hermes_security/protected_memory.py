"""Wipeable, page-locked custody for a retained vault key.

The retained key lives only in a :class:`hermes_platform.memory_protection.LockedRegion`
(``mlock``; on Linux also excluded from core dumps where the host grants it). Custody fails closed:
if the host refuses ``mlock`` the key is never written and :class:`KeyCustodyUnavailable` is
raised. Every read is an explicit immutable snapshot taken under the custody lock, so a concurrent
:meth:`KeyCustody.wipe` either happens first (the read raises) or after (the snapshot is the real
key); nothing ever observes a half-wiped or zeroed key.

Limits: snapshots, the immutable ``bytes`` a KDF returned before custody, and copies a native
library makes are ordinary transient objects released only by the garbage collector. Abandoned
custody is wiped when collected and at normal interpreter exit; a ``SIGKILL``/crash wipes nothing
(the kernel frees the pages). A same-user debugger or native process can still read the key.
"""
from __future__ import annotations

import errno
import threading
import weakref

from hermes_platform.memory_protection import LockedRegion, memlock_limits, restrict_process_core_dumps
from hermes_security.errors import VaultError, VaultLockedError

_core_restricted = threading.Event()


class KeyCustodyUnavailable(VaultError):
    """The host refused to keep the key in locked memory; the unlock must not proceed."""


def _refusal(region: LockedRegion) -> KeyCustodyUnavailable:
    reason = errno.errorcode.get(region.lock_errno, "unknown error")
    soft, _hard = memlock_limits()
    return KeyCustodyUnavailable(
        f"The vault key cannot be kept in locked memory: mlock was refused ({reason}; memlock soft limit "
        f"{soft} bytes). Raise this user's locked-memory limit (ulimit -l) and unlock again."
    )


class KeyCustody:
    """One retained key; reads are snapshots and refuse after :meth:`wipe`."""

    __slots__ = ("__weakref__", "_finalizer", "_lock", "_region")

    def __init__(self, key) -> None:
        source = memoryview(key)
        if not _core_restricted.is_set():
            restrict_process_core_dumps()
            _core_restricted.set()
        region = LockedRegion(source.nbytes)
        if not region.locked:
            region.wipe()  # never written: the key only ever enters locked pages
            raise _refusal(region)
        region.write(source)
        self._lock = threading.RLock()
        self._region: LockedRegion | None = region
        self._finalizer = weakref.finalize(self, region.wipe)  # abandoned custody / normal exit

    @property
    def held(self) -> bool:
        return self._region is not None

    @property
    def dontdump(self) -> bool:
        region = self._region
        return region is not None and region.dontdump

    def export_copy(self) -> bytes:
        """An immutable snapshot of the live key; :meth:`wipe` cannot reach it."""
        with self._lock:
            if self._region is None:
                raise VaultLockedError("The vault key was wiped by a lock")
            with self._region.view() as view:
                return bytes(view)

    def wipe(self) -> None:
        with self._lock:
            self._region = None
            self._finalizer()

    def __repr__(self) -> str:
        return "<KeyCustody held>" if self.held else "<KeyCustody wiped>"

    def __reduce__(self):
        raise TypeError("Vault key custody cannot be pickled")
