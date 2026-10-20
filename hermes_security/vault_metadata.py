"""Serialized, owner-checked publication of vault credential metadata."""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import stat
import threading
import time

from hermes_security.errors import VaultError, VaultIntegrityError, VaultNotInitializedError

_WRITERS = threading.RLock()
_LOCK_TIMEOUT = 10.0


def _owned_regular(path: Path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise VaultIntegrityError(f"Vault credential path must be an owned private regular file: {path}")
    return info


def exact_home(home: Path | str) -> Path:
    requested = Path(home).expanduser()
    if requested.is_symlink():
        raise VaultIntegrityError("A vault credential operation cannot target a symlinked home")
    result = requested.resolve()
    try:
        info = result.stat()
    except FileNotFoundError as exc:
        raise VaultNotInitializedError(f"No vault owned by the exact requested home: {result}") from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise VaultIntegrityError("Vault home must be owned by this user and not publicly writable")
    try:
        _owned_regular(result / ".hermes-vault")
    except FileNotFoundError as exc:
        raise VaultNotInitializedError(f"No vault owned by the exact requested home: {result}") from exc
    return result


class MetadataWriter:
    def __init__(self, home, unlocked):
        from hermes_security import vault
        from hermes_security.passphrase import verify_master_key

        self.home = exact_home(home)
        self.path = self.home / ".hermes-vault"
        self.original = self.path.read_bytes()
        self.directory = self.home.stat()
        self.meta = vault._load_meta(self.home)
        if unlocked.home != self.home:
            raise VaultIntegrityError("The unlocked vault does not own this metadata")
        verify_master_key(self.meta, unlocked.master_key)
        self.key = unlocked.master_key

    def commit(self, meta):
        from hermes_security import vault
        from hermes_security.passphrase import authenticate_metadata

        exact_home(self.home)
        directory = self.home.stat()
        if ((directory.st_dev, directory.st_ino) != (self.directory.st_dev, self.directory.st_ino)
                or self.path.read_bytes() != self.original):
            raise VaultIntegrityError("Vault metadata changed outside the credential transaction")
        data = json.dumps(authenticate_metadata(meta, self.key), sort_keys=True).encode("utf-8")
        vault._atomic_write(self.path, data)


@contextlib.contextmanager
def writer(home: Path | str, unlocked):
    """All credential writers share this never-unlinked inode and re-read under its lock."""
    from pm.filesystem import lock_fd

    deadline = time.monotonic() + _LOCK_TIMEOUT
    if not _WRITERS.acquire(timeout=_LOCK_TIMEOUT):
        raise VaultError("Vault credential metadata is busy; try again after the current operation finishes")
    try:
        home = exact_home(home)
        path = home / ".hermes-vault.lock"
        if path.is_symlink():
            raise VaultIntegrityError("Vault metadata lock cannot be a symlink")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            _owned_regular(path)
            if not lock_fd(fd, wait=True, timeout=max(0, deadline - time.monotonic())):
                raise VaultError("Vault credential metadata is busy; try again after the current operation finishes")
            info = _owned_regular(path)
            held = os.fstat(fd)
            if (info.st_dev, info.st_ino) != (held.st_dev, held.st_ino):
                raise VaultIntegrityError("Vault metadata lock was replaced")
            yield MetadataWriter(home, unlocked)
        finally:
            os.close(fd)
    finally:
        _WRITERS.release()
