"""Restore admission uses effective RAM, not a RAMfs zero-block fiction or host-sized free space.

Only kernel-fact input seams are supplied here; no host OS is faked and no browser is launched.
The actual RAMfs/browser cold-restore proof belongs in a declared disposable native environment.
"""
from __future__ import annotations

from io import StringIO
import hashlib
from pathlib import Path
import secrets
from types import SimpleNamespace

import pytest

from hermes_cli import browser_profile_store as store
from hermes_platform import runtime_health
from hermes_platform.memory_protection import RamProof
from hermes_platform.memory_capacity import CapacityBudget
from hermes_security import browser_profile_archive as archive
from hermes_security import vault
from tests.hermes_cli.browser_profile_env import exited_browser, owned_profile_env, reset_owned_profiles

MIB = 1024 * 1024


def _facts(monkeypatch, *, blocks, available, limit, used):
    monkeypatch.setattr(store, "_prove", lambda _path: RamProof(True, "ramfs", "synthetic kernel fact input"))
    monkeypatch.setattr(store.os, "statvfs", lambda _path: SimpleNamespace(
        f_blocks=blocks, f_bavail=available, f_frsize=4096))
    monkeypatch.setattr(runtime_health, "sample_memory", lambda: {
        "mem_total_kib": 8 * 1024 * 1024, "mem_available_kib": 4 * 1024 * 1024})
    values = {
        "/proc/self/cgroup": "0::/limited/worker\n",
        "/proc/self/mountinfo": "36 25 0:31 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n",
        "/sys/fs/cgroup/memory.max": "max\n",
        "/sys/fs/cgroup/limited/memory.max": str(limit),
        "/sys/fs/cgroup/limited/memory.current": str(used),
        "/sys/fs/cgroup/limited/worker/memory.max": "max\n",
    }
    original = Path.open

    def read(path, *args, **kwargs):
        if str(path) in values:
            return StringIO(values[str(path)])
        if str(path).startswith("/sys/fs/cgroup"):
            raise FileNotFoundError(str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", read)


@pytest.mark.platforms("linux")
def test_zero_block_ramfs_admits_restore_with_known_effective_memory(monkeypatch, tmp_path):
    _facts(monkeypatch, blocks=0, available=0, limit=512 * MIB, used=128 * MIB)
    store._require_capacity(tmp_path, MIB)


@pytest.mark.platforms("linux")
def test_finite_filesystem_does_not_override_exhausted_cgroup_budget(monkeypatch, tmp_path):
    _facts(monkeypatch, blocks=1024 * 1024, available=1024 * 1024,
           limit=256 * MIB, used=240 * MIB)
    with pytest.raises(store.BrowserProfileError, match="RAM|memory"):
        store._require_capacity(tmp_path, MIB)


def test_insufficient_budget_retains_authenticated_archive_and_never_opens_empty_profile(tmp_path, monkeypatch):
    home, _ram = owned_profile_env(tmp_path, monkeypatch)
    private = secrets.token_bytes(33)
    profile = store.owned_profile(archive.DEBUG_SLOT)
    try:
        with store.prepare(profile, {}) as active:
            (active / "private.bin").write_bytes(private)
            store.record_launch(profile, exited_browser())
        assert store.settle(home=home) == {archive.DEBUG_SLOT: "saved"}
        [stored] = archive.archive_paths(home)
        before = hashlib.sha256(stored.read_bytes()).digest()
        assert private not in stored.read_bytes()
        monkeypatch.setattr(store, "_capacity", lambda _path, **_kwargs: CapacityBudget(0, "known exhausted budget"))
        with pytest.raises(store.BrowserProfileError, match="RAM budget"):
            with store.prepare(profile, {}):
                pytest.fail("Insufficient capacity must not launch an empty replacement profile")
        assert hashlib.sha256(stored.read_bytes()).digest() == before
        assert not profile.path.exists() and vault.is_unlocked(home)
        monkeypatch.setattr(store, "_capacity", lambda _path, **_kwargs: CapacityBudget(8 * 1024**3, "unit budget"))
        with store.prepare(profile, {}) as restored:
            assert (restored / "private.bin").read_bytes() == private
    finally:
        reset_owned_profiles()
