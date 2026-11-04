"""Short private IPC metadata directories, independent of profile scratch retention."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile


def private_ipc_directory(*, prefix: str) -> tempfile.TemporaryDirectory:
    """Own only socket/FIFO nodes and empty lock metadata here, never credential files.

    Explicit /tmp avoids both cached tempfile defaults and a profile's idle-pruned
    scratch tree. Its short native spelling fits macOS's stricter AF_UNIX limit.
    Normalize the fresh directory to 0700: group-managed temp roots can add
    an inherited setgid bit. Only its owner removes it on close.
    """
    from hermes_platform.host.facts import os_family
    if os_family() not in {"linux", "darwin"}:
        raise OSError("private filesystem IPC is supported only on Linux and macOS")
    root = Path("/tmp")  # no-tmp: ok -- public IPC inode metadata only, never key or credential payload files
    if not root.is_dir():
        raise OSError("the native short IPC directory is unavailable")
    directory = tempfile.TemporaryDirectory(prefix=prefix, dir=str(root))
    try:
        descriptor = os.open(directory.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            os.fchmod(descriptor, 0o700)
        finally:
            os.close(descriptor)
    except BaseException:
        directory.cleanup()
        raise
    return directory
