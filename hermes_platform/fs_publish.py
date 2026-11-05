"""Native atomic directory publication without replacing another owner's path."""

import ctypes
import errno
import os
from pathlib import Path

from hermes_platform.host.facts import os_family


def rename_noreplace(source: Path, destination: Path) -> None:
    """Move a directory atomically, refusing even an existing empty destination.

    An existence check followed by ``rename`` cannot enforce this on POSIX. If the
    native exclusive primitive is unavailable, retain the source and fail closed.
    """
    paths = (os.fsencode(source), os.fsencode(destination))
    if any(b"\0" in path for path in paths):
        raise ValueError("Directory publication paths cannot contain NUL")
    family = os_family()
    if family == "win32":
        os.rename(source, destination)  # Windows rename already refuses an existing target.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    symbol = {"linux": "renameat2", "darwin": "renamex_np"}.get(family)
    rename = getattr(libc, symbol, None) if symbol else None
    if rename is None:
        raise OSError(errno.ENOSYS, "Atomic exclusive directory rename is unavailable", str(destination))
    rename.restype = ctypes.c_int
    if family == "linux":
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = rename(-100, paths[0], -100, paths[1], 1)  # AT_FDCWD, RENAME_NOREPLACE
    else:
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = rename(*paths, 4)  # RENAME_EXCL
    if result:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))
