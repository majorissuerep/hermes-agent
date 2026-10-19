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
