"""Live, conservative RAM admission facts; an observation, never a memory reservation.

RAMfs reports zero filesystem blocks because it has no filesystem capacity counter. Its budget
must still fit MemAvailable and every readable applicable cgroup hard limit. Finite filesystems
add their own free-space bound. Unknown consequential facts never become unlimited capacity.
Only visible cgroup ancestry is observable; hidden ancestors and concurrent consumption prevent
this snapshot from promising that a later allocation cannot encounter memory pressure.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import sys

from hermes_platform import runtime_health
from hermes_platform.memory_protection import RamProof, _unescape_mount_field, ram_only_directory_proof


@dataclass(frozen=True)
class CapacityBudget:
    available_bytes: int | None
    reason: str


def _read(path: Path, bound: int = 65536) -> str:
    with path.open(encoding="utf-8") as handle:
        value = handle.read(bound + 1)
    if len(value) > bound:
        raise ValueError("kernel memory facts exceed their read bound")
    return value.strip()


def _absolute(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts or "\x00" in value:
        raise ValueError("invalid cgroup path")
    return path


def _membership(text: str) -> tuple[int, PurePosixPath] | None:
    groups = []
    for line in text.splitlines():
        hierarchy, controllers, member = line.split(":", 2)
        if not hierarchy.isascii() or not hierarchy.isdecimal():
            raise ValueError("invalid cgroup hierarchy")
        if (hierarchy == "0" and controllers) or (hierarchy != "0" and not controllers):
            raise ValueError("invalid cgroup membership")
        if "memory" in controllers.split(","):
            groups.append((1, _absolute(member)))
        elif hierarchy == "0" and not controllers:
            groups.append((2, _absolute(member)))
    if not text or len({version for version, _path in groups}) != len(groups):
        raise ValueError("ambiguous cgroup membership")
    # Hybrid systems can put memory on v1 while other controllers use v2.
    return next((group for group in groups if group[0] == 1), groups[0] if groups else None)


def _route(version: int, member: PurePosixPath, mountinfo: str) -> tuple[Path, Path, bool]:
    candidates = []
    for line in mountinfo.splitlines():
        fields = line.split()
        if "-" not in fields[6:]:
            continue
        separator = fields.index("-", 6)
        if len(fields) < separator + 4:
            continue
        kind, options = fields[separator + 1], fields[separator + 3].split(",")
        if not (kind == "cgroup2" if version == 2 else kind == "cgroup" and "memory" in options):
            continue
        root = _absolute(_unescape_mount_field(fields[3]))
        if member.is_relative_to(root):
            mount = Path(str(_absolute(_unescape_mount_field(fields[4]))))
            candidates.append((len(root.parts), mount, mount / str(member.relative_to(root)), str(root) == "/"))
    if not candidates:
        raise ValueError("memory cgroup mount is unavailable")
    _depth, mount, current, mounted_from_root = max(candidates, key=lambda item: item[0])
    return mount, current, mounted_from_root


def _number(text: str) -> int:
    if not text.isascii() or not text.isdecimal():
        raise ValueError("invalid memory counter")
    return int(text)


def _limit(directory: Path, root: Path, version: int, mounted_from_root: bool) -> int | None:
    maximum = "memory.max" if version == 2 else "memory.limit_in_bytes"
    try:
        raw = _read(directory / maximum)
    except FileNotFoundError:
        # A real v2 hierarchy root has no memory.max. At an inactive child controller, its
        # absence is established by cgroup.controllers; missing active-controller facts refuse.
        if version != 2:
            raise
        controllers = _read(directory / "cgroup.controllers").split()
        if "memory" in controllers and not (directory == root and mounted_from_root):
            raise
        return None
    if version == 2 and raw == "max":
        return None
    limit = _number(raw)
    if version == 1 and limit >= 1 << 60:  # documented v1 PAGE_COUNTER_MAX sentinel
        return None
    usage = _read(directory / ("memory.current" if version == 2 else "memory.usage_in_bytes"))
    return max(0, limit - _number(usage))


def cgroup_remaining() -> int | None:
    """Minimum applicable ancestor hard-limit headroom; None means established unconstrained.

    Malformed, inaccessible or unmapped applicable facts raise rather than imply no constraint.
    """
    membership = _membership(_read(Path("/proc/self/cgroup")))
    if membership is None:
        return None
    version, member = membership
    root, directory, mounted_from_root = _route(version, member, _read(Path("/proc/self/mountinfo"), 1024 * 1024))
    remaining = []
    while True:
        limit = _limit(directory, root, version, mounted_from_root)
        if limit is not None:
            remaining.append(limit)
        if directory == root:
            return min(remaining) if remaining else None
        directory = directory.parent


def capacity_budget(path: Path, *, proof: RamProof | None = None) -> CapacityBudget:
    """Snapshot min(MemAvailable, applicable cgroup headroom, finite filesystem free bytes)."""
    if not sys.platform.startswith("linux"):
        return CapacityBudget(None, "effective protected RAM capacity is not available on this host")
    memory = runtime_health.sample_memory()
    total, available = memory.get("mem_total_kib"), memory.get("mem_available_kib")
    if (type(total) is not int or type(available) is not int or total <= 0
            or not 0 <= available <= total):
        return CapacityBudget(None, "MemAvailable could not be established")
    try:
        group = cgroup_remaining()
        info = os.statvfs(path)
        if not 0 <= info.f_bavail <= info.f_blocks or info.f_frsize <= 0:
            raise ValueError("invalid filesystem capacity counters")
    except (OSError, ValueError):
        return CapacityBudget(None, "filesystem or applicable cgroup memory capacity could not be established")
    if not info.f_blocks:
        proof = proof if proof is not None else ram_only_directory_proof(path)
        if not proof.proven or proof.filesystem != "ramfs":
            return CapacityBudget(None, "unreported filesystem capacity is not a proven RAMfs")
    bounds = [available * 1024]
    if group is not None:
        bounds.append(group)
    if info.f_blocks:
        bounds.append(info.f_bavail * info.f_frsize)
    return CapacityBudget(min(bounds), "minimum observed RAM, cgroup and finite filesystem headroom")
