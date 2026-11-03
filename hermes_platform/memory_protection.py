"""Native memory-protection primitives and facts for this control host.

A :class:`LockedRegion` is a private anonymous mapping that the kernel is asked to keep
resident (``mlock``) and out of core dumps (Linux ``MADV_DONTDUMP``). Each request reports what
the host actually granted; nothing here pretends a refused request succeeded. These primitives
protect only bytes stored in the region: they cannot reach copies the interpreter or a native
library made elsewhere, and they do not defend against a same-user debugger or native process.
"""
from __future__ import annotations

import ctypes
import mmap
import os
import re
import sys
from dataclasses import dataclass

_PAGE = mmap.PAGESIZE


def _libc() -> ctypes.CDLL:
    return ctypes.CDLL(None, use_errno=True)


def _page_span(size: int) -> int:
    return -(-size // _PAGE) * _PAGE


class LockedRegion:
    """Owner of one page-aligned anonymous mapping; :meth:`wipe` zeroes, unlocks and unmaps it."""

    __slots__ = ("_address", "_map", "dontdump", "lock_errno", "locked", "size")

    def __init__(self, size: int) -> None:
        if type(size) is not int or size <= 0:
            raise ValueError("A locked region needs a positive byte size")
        span = _page_span(size)
        self.size = size
        self._map: mmap.mmap | None = mmap.mmap(-1, span, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
        anchor = (ctypes.c_char * span).from_buffer(self._map)
        self._address = ctypes.addressof(anchor)
        del anchor  # release the buffer export so wipe() can unmap
        self.locked = _libc().mlock(ctypes.c_void_p(self._address), ctypes.c_size_t(span)) == 0
        self.lock_errno = 0 if self.locked else ctypes.get_errno()
        self.dontdump = _exclude_from_core_dumps(self._map)

    @property
    def closed(self) -> bool:
        return self._map is None

    def write(self, data) -> None:
        if self._map is None:
            raise ValueError("The locked region was wiped")
        source = memoryview(data).cast("B")
        if source.nbytes != self.size:
            raise ValueError("Locked region writes must fill the region exactly")
        self._map[: self.size] = source

    def view(self) -> memoryview:
        """Read-only view; valid (zeroed) even if the region is wiped while it is alive."""
        if self._map is None:
            raise ValueError("The locked region was wiped")
        return memoryview(self._map)[: self.size].toreadonly()

    def wipe(self) -> None:
        mapping, self._map = self._map, None
        if mapping is None:
            return
        span = len(mapping)
        ctypes.memset(self._address, 0, span)
        if self.locked:
            _libc().munlock(ctypes.c_void_p(self._address), ctypes.c_size_t(span))
        try:
            mapping.close()
        except BufferError:
            # A caller still holds a view: the zeroed pages are unmapped when it is released.
            return

    def __repr__(self) -> str:
        state = "wiped" if self._map is None else f"{self.size} bytes"
        return f"<LockedRegion {state} locked={self.locked} dontdump={self.dontdump}>"

    def __reduce__(self):
        raise TypeError("Locked memory cannot be pickled")


def _exclude_from_core_dumps(mapping: mmap.mmap) -> bool:
    advice = getattr(mmap, "MADV_DONTDUMP", None)
    if advice is None:
        return False
    try:
        mapping.madvise(advice)
    except OSError:
        return False
    return True


def restrict_process_core_dumps() -> bool:
    """Lower this process's soft core limit to zero; the hard limit and host policy stay unchanged.

    Children inherit the lowered soft limit and may raise it again up to the hard limit.
    """
    import resource

    try:
        _soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
        resource.setrlimit(resource.RLIMIT_CORE, (0, hard))
    except (OSError, ValueError):
        return False
    return True


def core_dump_limits() -> tuple[int, int]:
    import resource

    return resource.getrlimit(resource.RLIMIT_CORE)


def memlock_limits() -> tuple[int, int]:
    import resource

    return resource.getrlimit(resource.RLIMIT_MEMLOCK)


# --- Swap and RAM-only mount facts ------------------------------------------------------------
# tmpfs pages may be written to swap, so tmpfs alone is not secure RAM. Only ramfs, or tmpfs with
# its own ``noswap`` option or with provably no active swap, counts. Unknown is never proven.

_SWAPS_HEADER = "Filename"
_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")


@dataclass(frozen=True)
class RamProof:
    """Whether a directory is provably backed by never-swapped RAM; ``reason`` is public text."""

    proven: bool
    filesystem: str
    reason: str


def parse_swaps(text: str) -> str:
    """``/proc/swaps`` text -> ``"none"`` (header only), ``"active"``, or ``"unknown"``."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith(_SWAPS_HEADER):
        return "unknown"
    return "active" if any(line.strip() for line in lines[1:]) else "none"


def swap_state() -> str:
    """Linux swap state of this control host; other hosts report ``"unknown"``."""
    if not sys.platform.startswith("linux"):
        return "unknown"
    try:
        with open("/proc/swaps", encoding="utf-8") as handle:
            return parse_swaps(handle.read())
    except OSError:
        return "unknown"


def _unescape_mount_field(field: str) -> str:
    return _OCTAL_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), field)


def _within(path: str, mount_point: str) -> bool:
    return mount_point == "/" or path == mount_point or path.startswith(mount_point + "/")


def _containing_mount(mountinfo: str, real_path: str) -> tuple[str, frozenset[str]] | None:
    """Return ``(fstype, super options)`` of the last-mounted longest mount point over the path."""
    best: tuple[int, str, frozenset[str]] | None = None
    for line in mountinfo.splitlines():
        fields = line.split()
        if "-" not in fields[6:]:
            continue
        separator = fields.index("-", 6)
        if len(fields) < separator + 4:
            continue
        mount_point = _unescape_mount_field(fields[4])
        if _within(real_path, mount_point) and (best is None or len(mount_point) >= best[0]):
            options = frozenset(fields[separator + 3].split(","))
            best = (len(mount_point), fields[separator + 1], options)
    return None if best is None else best[1:]


def prove_ram_only(mountinfo: str, real_path: str, swap: str) -> RamProof:
    """Decide from mountinfo text and swap state whether ``real_path`` lives in never-swapped RAM."""
    mount = _containing_mount(mountinfo, real_path)
    if mount is None:
        return RamProof(False, "unknown", "no readable mount entry contains the directory")
    fstype, options = mount
    if fstype == "ramfs":
        return RamProof(True, fstype, "ramfs pages are never swapped")
    if fstype != "tmpfs":
        return RamProof(False, fstype, f"{fstype} is not a RAM-only filesystem")
    if "noswap" in options:
        return RamProof(True, fstype, "tmpfs is mounted with noswap")
    if swap == "none":
        return RamProof(True, fstype, "tmpfs with no active swap on this host")
    if swap == "active":
        return RamProof(False, fstype, "tmpfs pages can be written to active swap")
    return RamProof(False, fstype, "tmpfs with an unknown swap state")


def per_user_runtime_dir() -> str | None:
    """This session's per-user runtime directory (``XDG_RUNTIME_DIR``) when it is absolute.

    Only a candidate location: its backing must still be proven with :func:`ram_only_directory_proof`.
    """
    candidate = os.environ.get("XDG_RUNTIME_DIR", "")
    return candidate if os.path.isabs(candidate) else None


def ram_only_directory_proof(path: str | os.PathLike[str]) -> RamProof:
    """Live proof for ``path`` on this control host; only Linux can prove a RAM-only mount."""
    if not sys.platform.startswith("linux"):
        return RamProof(False, "unknown", "RAM-only mount proofs are not available on this host")
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as handle:
            mountinfo = handle.read()
    except OSError:
        return RamProof(False, "unknown", "the mount table is unreadable")
    return prove_ram_only(mountinfo, os.path.realpath(path), swap_state())
