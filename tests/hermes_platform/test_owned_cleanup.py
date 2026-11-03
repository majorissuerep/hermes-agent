"""A supervisor can remove its exact directory, never a replacement or a symlink."""
import os

import pytest

from hermes_platform.owned_cleanup import capture_directories, remove_directories

pytestmark = pytest.mark.platforms("linux", "macos")


def private_directory(path):
    path.mkdir(mode=0o700)
    (path / "synthetic-data").write_text("synthetic", encoding="utf-8")
    return path.resolve()


def test_owned_cleanup_removes_only_the_captured_directory(tmp_path):
    owned = private_directory(tmp_path / "owned")
    sibling = private_directory(tmp_path / "sibling")
    records = capture_directories([owned])
    remove_directories(records)
    assert not owned.exists()
    assert (sibling / "synthetic-data").read_text(encoding="utf-8") == "synthetic"
    with pytest.raises(OSError):
        os.fstat(records[0]["parent"])


def test_replaced_directory_is_retained_and_the_descriptor_is_closed(tmp_path):
    owned = private_directory(tmp_path / "owned")
    records = capture_directories([owned])
    moved = owned.with_name("moved")
    owned.rename(moved)
    private_directory(owned)
    with pytest.raises(RuntimeError, match="identity changed"):
        remove_directories(records)
    assert (owned / "synthetic-data").read_text(encoding="utf-8") == "synthetic"
    assert (moved / "synthetic-data").read_text(encoding="utf-8") == "synthetic"
    with pytest.raises(OSError):
        os.fstat(records[0]["parent"])


def test_symlink_and_parent_alias_cannot_be_entrusted_for_cleanup(tmp_path):
    owned = private_directory(tmp_path / "owned")
    link = tmp_path / "alias"
    link.symlink_to(owned, target_is_directory=True)
    for target in (link, owned / ".."):
        with pytest.raises(ValueError, match="private directory"):
            capture_directories([target])
    assert (owned / "synthetic-data").read_text(encoding="utf-8") == "synthetic"
