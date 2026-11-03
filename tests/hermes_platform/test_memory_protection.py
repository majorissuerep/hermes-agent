"""Memory-protection facts: swap state, RAM-only mount proofs, and locked regions.

The parsing contracts run on synthetic ``/proc/swaps`` and ``/proc/self/mountinfo`` text so every
host exercises them; the native arms compare the live readers with the host's own files.
"""
from __future__ import annotations

import mmap
import os
import pickle
import resource
from pathlib import Path

import pytest

from hermes_platform.memory_protection import (
    LockedRegion,
    RamProof,
    parse_swaps,
    prove_ram_only,
    ram_only_directory_proof,
    swap_state,
)

_SWAPS_HEADER = "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
_SWAPS_ACTIVE = _SWAPS_HEADER + "/dev/sdz                                partition\t8388608\t\t0\t\t-2\n"

_MOUNTINFO = (
    "22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw\n"
    "30 22 0:40 / /run rw,nosuid,nodev shared:5 - tmpfs tmpfs rw,mode=755\n"
    "31 30 0:41 / /run/keys rw,nosuid shared:6 - ramfs ramfs rw,mode=700\n"
    "32 30 0:42 / /run/noswap rw,nosuid shared:7 - tmpfs tmpfs rw,noswap,mode=700\n"
    "33 22 0:43 / /srv/my\\040ram rw shared:8 - ramfs ramfs rw\n"
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (_SWAPS_HEADER, "none"),
        (_SWAPS_HEADER + "\n", "none"),
        (_SWAPS_ACTIVE, "active"),
        ("", "unknown"),
        ("garbage without a header\n", "unknown"),
    ],
)
def test_parse_swaps(text: str, expected: str) -> None:
    assert parse_swaps(text) == expected


@pytest.mark.parametrize(
    ("path", "swap", "proven", "filesystem"),
    [
        ("/run/keys/agent", "active", True, "ramfs"),
        ("/run/keys/agent", "unknown", True, "ramfs"),
        ("/run/noswap/agent", "active", True, "tmpfs"),
        ("/run/agent", "none", True, "tmpfs"),
        ("/run/agent", "active", False, "tmpfs"),
        ("/run/agent", "unknown", False, "tmpfs"),
        ("/home/user/work", "none", False, "ext4"),
        ("/srv/my ram/agent", "active", True, "ramfs"),
        ("/srv/my ramdisk", "none", False, "ext4"),
        ("/run/keysX", "active", False, "tmpfs"),
    ],
)
def test_prove_ram_only_from_mountinfo(path: str, swap: str, proven: bool, filesystem: str) -> None:
    proof = prove_ram_only(_MOUNTINFO, path, swap)
    assert isinstance(proof, RamProof)
    assert (proof.proven, proof.filesystem) == (proven, filesystem)
    assert proof.reason


def test_unparseable_mountinfo_is_not_proven() -> None:
    proof = prove_ram_only("not a mount table\n", "/run/keys/agent", "none")
    assert not proof.proven
    assert proof.filesystem == "unknown"


def test_later_mount_on_same_point_wins() -> None:
    stacked = _MOUNTINFO + "40 30 0:50 / /run/keys rw shared:9 - ext4 /dev/sdb1 rw\n"
    assert prove_ram_only(stacked, "/run/keys/agent", "none") == prove_ram_only(
        "40 30 0:50 / /run/keys rw shared:9 - ext4 /dev/sdb1 rw\n", "/run/keys/agent", "none"
    )
    assert not prove_ram_only(stacked, "/run/keys/agent", "none").proven


def _expected_swap_state(text: str) -> str:
    entries = [line for line in text.splitlines()[1:] if line.strip()]
    return "active" if entries else "none"


@pytest.mark.platforms("linux")
def test_swap_state_matches_proc_swaps() -> None:
    assert swap_state() == _expected_swap_state(Path("/proc/swaps").read_text())


@pytest.mark.platforms("linux")
def test_directory_proof_agrees_with_host_mount_table(tmp_path: Path) -> None:
    mountinfo = Path("/proc/self/mountinfo").read_text()
    live = ram_only_directory_proof(tmp_path)
    assert live == prove_ram_only(mountinfo, os.path.realpath(tmp_path), swap_state())
    if live.filesystem == "tmpfs" and swap_state() != "none":
        assert not live.proven


@pytest.mark.platforms("linux")
def test_proc_is_never_a_ram_proof() -> None:
    proof = ram_only_directory_proof("/proc/self")
    assert (proof.proven, proof.filesystem) == (False, "proc")


@pytest.mark.platforms("macos")
def test_macos_has_no_ram_proof(tmp_path: Path) -> None:
    assert swap_state() == "unknown"
    assert not ram_only_directory_proof(tmp_path).proven


def _locked_kib() -> int:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmLck:"):
            return int(line.split()[1])
    raise AssertionError("VmLck missing from /proc/self/status")


def _memlock_allows_one_page() -> bool:
    soft, _hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    return soft == resource.RLIM_INFINITY or soft >= mmap.PAGESIZE


@pytest.mark.platforms("linux")
@pytest.mark.skipif(not _memlock_allows_one_page(), reason="RLIMIT_MEMLOCK soft limit is below one page")
def test_locked_region_is_locked_undumpable_and_wiped() -> None:
    pattern = bytes(range(32))
    before = _locked_kib()
    region = LockedRegion(32)
    assert region.locked and region.dontdump
    assert _locked_kib() - before >= mmap.PAGESIZE // 1024
    region.write(pattern)
    view = region.view()
    assert bytes(view) == pattern
    assert pattern.hex() not in repr(region)
    with pytest.raises(TypeError):
        pickle.dumps(region)
    region.wipe()
    assert bytes(view) == bytes(32)
    assert _locked_kib() == before
    assert pattern.hex() not in repr(region)
    view.release()
