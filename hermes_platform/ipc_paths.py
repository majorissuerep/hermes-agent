"""Short private IPC metadata directories, independent of profile scratch retention."""
from __future__ import annotations

from pathlib import Path
import tempfile


def private_ipc_directory(*, prefix: str) -> tempfile.TemporaryDirectory:
    """Own only socket/FIFO nodes and empty lock metadata here, never credential files.

    Explicit /tmp avoids both cached tempfile defaults and a profile's idle-pruned
    scratch tree. Its short native spelling fits macOS's stricter AF_UNIX limit.
    mkdtemp creates a fresh 0700 directory; only its owner removes it on close.
    """
    from hermes_platform.host.facts import os_family
    if os_family() not in {"linux", "darwin"}:
        raise OSError("private filesystem IPC is supported only on Linux and macOS")
    root = Path("/tmp")  # no-tmp: ok -- public IPC inode metadata only, never key or credential payload files
    if not root.is_dir():
        raise OSError("the native short IPC directory is unavailable")
    return tempfile.TemporaryDirectory(prefix=prefix, dir=str(root))
