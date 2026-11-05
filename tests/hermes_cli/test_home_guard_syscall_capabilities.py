"""Filesystem guards preserve native syscall capabilities and restore on teardown."""
import os
import shutil

import pytest

from tests.home_io_guard import HomeIOGuard


@pytest.mark.platforms("linux", "macos")
def test_guarded_symlink_copy_preserves_capabilities_and_protection(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "secret").write_text("unchanged", encoding="utf-8")
    (source / "link").symlink_to("secret")
    protected = tmp_path / "protected"
    protected.mkdir()
    denied = protected / "secret"
    denied.write_text("protected", encoding="utf-8")
    before_stat = os.stat
    before_sets = {name: getattr(os, name) for name in
                   ("supports_follow_symlinks", "supports_dir_fd", "supports_fd", "supports_effective_ids")}
    with pytest.MonkeyPatch.context() as patch:
        HomeIOGuard(lambda: (protected,)).install(patch)
        copied = tmp_path / "copied"
        shutil.copytree(source, copied, symlinks=True,
                        copy_function=lambda src, dst: shutil.copy2(src, dst))
        assert (copied / "link").is_symlink()
        assert (copied / "link").read_text(encoding="utf-8") == "unchanged"
        with pytest.raises(AssertionError, match="REAL hermes home"):
            denied.write_text("changed", encoding="utf-8")
    assert os.stat is before_stat
    assert all(getattr(os, name) is original for name, original in before_sets.items())
    assert denied.read_text(encoding="utf-8") == "protected"


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("operation", ["dup", "dup2"])
def test_directory_duplicates_preserve_relative_io_and_protected_paths(tmp_path, operation):
    source, installed = tmp_path / "source", tmp_path / "installed"
    source.mkdir()
    installed.mkdir()
    (source / "value").write_text("source intact")
    (installed / "value").write_text("installed intact")
    protected = []
    with pytest.MonkeyPatch.context() as patch:
        HomeIOGuard(lambda: protected, lambda: (str(installed),)).install(patch)
        descriptors = []
        try:
            for path in (source, installed):
                fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
                descriptors.append(fd)
                if operation == "dup":
                    duplicate = os.dup(fd)
                    descriptors.append(duplicate)
                else:
                    duplicate = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
                    descriptors.append(duplicate)
                    os.dup2(fd, duplicate)
                if path == source:
                    read = os.open("value", os.O_RDONLY, dir_fd=duplicate)
                    try:
                        assert os.read(read, 100) == b"source intact"
                    finally:
                        os.close(read)
                    protected.append(source)
                    with pytest.raises(AssertionError, match="REAL hermes home"):
                        os.stat("value", dir_fd=duplicate)
                else:
                    with pytest.raises(pytest.fail.Exception, match="REAL installed"):
                        os.open("value", os.O_WRONLY | os.O_TRUNC, dir_fd=duplicate)
        finally:
            for fd in descriptors:
                os.close(fd)
    assert (source / "value").read_text() == "source intact"
    assert (installed / "value").read_text() == "installed intact"


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("operation", ["dup", "dup2", "recycled"])
def test_unknown_or_recycled_descriptors_never_inherit_trusted_paths(tmp_path, operation):
    source, protected = tmp_path / "source", tmp_path / "protected"
    source.mkdir()
    protected.mkdir()
    (protected / "value").write_text("protected intact")
    raw_dup2 = os.dup2
    unknown = os.open(protected, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.MonkeyPatch.context() as patch:
            HomeIOGuard(lambda: (protected,)).install(patch)
            target = os.open(source, os.O_RDONLY | os.O_DIRECTORY)
            duplicate = None
            try:
                if operation == "dup":
                    duplicate = os.dup(unknown)
                    candidate = duplicate
                elif operation == "dup2":
                    os.dup2(unknown, target)
                    candidate = target
                else:
                    raw_dup2(unknown, target)  # an untracked native replacement of a known number
                    candidate = target
                with pytest.raises(AssertionError, match="untracked dir_fd"):
                    opened = os.open("value", os.O_RDONLY, dir_fd=candidate)
                    os.close(opened)  # no leaked descriptor if the guard incorrectly allows it
                if operation == "recycled":
                    duplicate = os.dup(target)
                    with pytest.raises(AssertionError, match="untracked dir_fd"):
                        os.stat("value", dir_fd=duplicate)
            finally:
                if duplicate is not None:
                    os.close(duplicate)
                os.close(target)
    finally:
        os.close(unknown)
    assert (protected / "value").read_text() == "protected intact"
