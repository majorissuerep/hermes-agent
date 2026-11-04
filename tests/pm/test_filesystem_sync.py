"""Native filesystem barriers preserve durability without flushing unrelated mounts."""
from __future__ import annotations

import errno
import os

import pytest

from pm import filesystem


@pytest.mark.platforms("linux")
def test_generation_barrier_flushes_each_supplied_filesystem_once(tmp_path, monkeypatch):
    tree = tmp_path / "generation"
    tree.mkdir()
    (tree / "complete").write_bytes(b"durable generation")
    alias = tmp_path / "store"
    alias.symlink_to(tree, target_is_directory=True)
    flushed = []
    native_sync = filesystem._sync_linux_filesystem

    def sync(fd):
        flushed.append(os.fstat(fd).st_dev)
        native_sync(fd)

    def unrelated():
        raise AssertionError("a generation must not flush unrelated filesystems")

    monkeypatch.setattr(filesystem, "_sync_linux_filesystem", sync)
    monkeypatch.setattr(os, "sync", unrelated)
    filesystem.sync_filesystems(tree, alias)
    assert flushed == [tree.stat().st_dev]


@pytest.mark.platforms("linux")
def test_native_writeback_errors_and_missing_roots_are_reported(tmp_path):
    with pytest.raises(OSError) as failed:
        filesystem._sync_linux_filesystem(-1)
    assert failed.value.errno == errno.EBADF
    with pytest.raises(FileNotFoundError):
        filesystem.sync_filesystems(tmp_path / "absent")
