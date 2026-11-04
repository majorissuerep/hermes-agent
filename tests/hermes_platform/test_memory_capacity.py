"""Conservative live-capacity decisions from supplied kernel facts, without allocating RAM."""
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_platform import memory_capacity as capacity
from hermes_platform import runtime_health
from hermes_platform.memory_protection import RamProof

MIB = 1024 * 1024
ROOT = "/sys/fs/cgroup/"
PARENT = ROOT + "limited/"
CHILD = PARENT + "worker/"

CASES = [
    ("ancestor-limit", {}, {}, (1048576, 524288), "ramfs", 384 * MIB),
    ("child-limit", {CHILD + "memory.max": str(192 * MIB), CHILD + "memory.current": str(64 * MIB)},
     {}, (1048576, 524288), "ramfs", 128 * MIB),
    ("root-unlimited", {"/proc/self/cgroup": "0::/", ROOT + "memory.max": None,
                        ROOT + "cgroup.controllers": "cpu memory"}, {}, (1048576, 524288), "ramfs", 2048 * MIB),
    ("missing-root-facts", {"/proc/self/cgroup": "0::/", ROOT + "memory.max": None},
     {}, (1048576, 524288), "ramfs", None),
    ("inactive-controller", {CHILD + "memory.max": None, CHILD + "cgroup.controllers": "cpu"},
     {}, (1048576, 524288), "ramfs", 384 * MIB),
    ("missing-active-counter", {CHILD + "memory.max": None, CHILD + "cgroup.controllers": "memory"},
     {}, (1048576, 524288), "ramfs", None),
    ("missing-usage", {PARENT + "memory.current": None}, {}, (1048576, 524288), "ramfs", None),
    ("malformed-limit", {PARENT + "memory.max": "unreadable"}, {}, (1048576, 524288), "ramfs", None),
    ("negative-usage", {PARENT + "memory.current": "-1"}, {}, (1048576, 524288), "ramfs", None),
    ("unknown-available", {}, {"mem_available_kib": None}, (1048576, 524288), "ramfs", None),
    ("actual-zero-available", {}, {"mem_available_kib": 0}, (1048576, 524288), "ramfs", 0),
    ("finite-filesystem-full", {}, {}, (1048576, 0), "tmpfs", 0),
    ("proven-ramfs-unreported", {}, {}, (0, 0), "ramfs", 384 * MIB),
    ("unproven-unreported", {}, {}, (0, 0), None, None),
    ("tmpfs-unreported", {}, {}, (0, 0), "tmpfs", None),
    ("invalid-membership", {"/proc/self/cgroup": "0::/../../outside"}, {}, (1048576, 524288), "ramfs", None),
    ("malformed-membership", {"/proc/self/cgroup": "unknown:cpu:/"}, {}, (1048576, 524288), "ramfs", None),
    ("v1-ancestor", {
        "/proc/self/cgroup": "5:memory:/limited/worker",
        "/proc/self/mountinfo": "36 25 0:31 / /sys/fs/cgroup rw - cgroup cgroup rw,memory",
        ROOT + "memory.limit_in_bytes": str((1 << 63) - 4096),
        PARENT + "memory.limit_in_bytes": str(256 * MIB), PARENT + "memory.usage_in_bytes": str(200 * MIB),
        CHILD + "memory.limit_in_bytes": str(512 * MIB), CHILD + "memory.usage_in_bytes": str(128 * MIB),
    }, {}, (1048576, 524288), "ramfs", 56 * MIB),
    ("mapped-subtree", {
        "/proc/self/mountinfo": "36 25 0:31 /limited /sys/fs/cgroup rw - cgroup2 cgroup rw",
        ROOT + "memory.max": str(192 * MIB), ROOT + "memory.current": str(64 * MIB),
        ROOT + "worker/memory.max": "max",
    }, {}, (1048576, 524288), "ramfs", 128 * MIB),
]


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("_name,edits,memory_edits,filesystem,proof_kind,expected", CASES,
                         ids=[row[0] for row in CASES])
def test_capacity_obeys_every_known_bound_and_refuses_unknowns(
        monkeypatch, tmp_path, _name, edits, memory_edits, filesystem, proof_kind, expected):
    values = {
        "/proc/self/cgroup": "0::/limited/worker",
        "/proc/self/mountinfo": "36 25 0:31 / /sys/fs/cgroup rw - cgroup2 cgroup rw",
        ROOT + "memory.max": "max", PARENT + "memory.max": str(512 * MIB),
        PARENT + "memory.current": str(128 * MIB), CHILD + "memory.max": "max",
    }
    values.update(edits)
    original = Path.open

    def read(path, *args, **kwargs):
        if str(path).startswith(("/proc/self/", ROOT)):
            value = values.get(str(path))
            if value is None:
                raise FileNotFoundError(str(path))
            return StringIO(value)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", read)
    memory = {"mem_total_kib": 8 * 1024 * 1024, "mem_available_kib": 4 * 1024 * 1024}
    memory.update(memory_edits)
    monkeypatch.setattr(runtime_health, "sample_memory", lambda: memory)
    monkeypatch.setattr(capacity.os, "statvfs", lambda _path: SimpleNamespace(
        f_blocks=filesystem[0], f_bavail=filesystem[1], f_frsize=4096))
    proof = RamProof(proof_kind is not None, proof_kind or "unknown", "supplied fact input")
    assert capacity.capacity_budget(tmp_path, proof=proof).available_bytes == expected
