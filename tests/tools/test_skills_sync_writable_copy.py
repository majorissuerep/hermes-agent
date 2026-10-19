"""Immutable installed skills become editable copies without losing executability."""

import stat

import pytest

from tools.skills_sync import _copy_dir


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("source_mode", [0o444, 0o555])
def test_readonly_package_modes_do_not_lock_user_skill_copy(tmp_path, source_mode):
    source = tmp_path / "package"
    source.mkdir()
    script = source / "script.sh"
    script.write_text("echo bundled\n")
    script.chmod(source_mode)
    source.chmod(0o555)
    destination = tmp_path / "user-skill"
    try:
        _copy_dir(source, destination)
        copied = destination / script.name
        assert copied.read_bytes() == script.read_bytes()
        assert copied.stat().st_mode & stat.S_IWUSR
        assert destination.stat().st_mode & stat.S_IWUSR
        assert copied.stat().st_mode & 0o111 == source_mode & 0o111
        copied.write_text("user edit\n")
        (destination / "user-note.txt").write_text("local addition")
        assert script.read_text() == "echo bundled\n"
        assert stat.S_IMODE(script.stat().st_mode) == source_mode
    finally:
        source.chmod(0o755)
        script.chmod(0o644)
        if destination.exists():
            destination.chmod(0o755)


@pytest.mark.platforms("linux", "macos")
def test_filling_partial_skill_preserves_user_bytes_and_permissions(tmp_path):
    from tools.skills_sync_optional import _copy_skill_tree

    source = tmp_path / "package"
    source.mkdir()
    (source / "user.txt").write_text("bundled replacement")
    (source / "SKILL.md").write_text("bundled skill")
    destination = tmp_path / "user-skill"
    destination.mkdir(mode=0o700)
    user_file = destination / "user.txt"
    user_file.write_text("user-owned edit")
    user_file.chmod(0o444)
    try:
        _copy_skill_tree(source, destination, missing_only=True)
        assert user_file.read_text() == "user-owned edit"
        assert stat.S_IMODE(user_file.stat().st_mode) == 0o444
        assert stat.S_IMODE(destination.stat().st_mode) == 0o700
        assert (destination / "SKILL.md").read_text() == "bundled skill"
        assert (destination / "SKILL.md").stat().st_mode & stat.S_IWUSR
    finally:
        user_file.chmod(0o644)
