"""Behavioral tests for the profile archive CI guard."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "ci"
    / "check_profile_archive_boundary.py"
)


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_clean_root_passes(tmp_path):
    result = _run(tmp_path)

    assert result.returncode == 0
    assert "No profile export archives" in result.stdout


def test_root_profile_archive_fails_without_printing_contents(tmp_path):
    default_archive = tmp_path / "default.tar.gz"
    alternate_archive = tmp_path / "backup.TGZ"
    default_archive.write_bytes(b"profile webhook secret must never be printed")
    alternate_archive.write_bytes(b"another archive")

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "default.tar.gz" in result.stdout
    assert "backup.TGZ" in result.stdout
    assert "profile webhook secret" not in result.stdout
    assert "another archive" not in result.stdout


def test_nested_profile_archive_is_also_rejected(tmp_path):
    nested = tmp_path / "fixtures"
    nested.mkdir()
    (nested / "fixture.tar.gz").write_bytes(b"test fixture")

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "fixtures/fixture.tar.gz" in result.stdout



def test_only_exact_reviewed_dependency_bytes_are_allowed(tmp_path):
    from scripts.ci.check_profile_archive_boundary import _REVIEWED_DEPENDENCY_ARCHIVES

    repository = Path(__file__).resolve().parents[2]
    for relative in _REVIEWED_DEPENDENCY_ARCHIVES:
        source = repository / relative
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    assert _run(tmp_path).returncode == 0

    target.write_bytes(target.read_bytes() + b"private profile secret")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert target.name in result.stdout
    assert "private profile secret" not in result.stdout


def test_reviewed_dependency_name_does_not_authorize_an_alias(tmp_path):
    from scripts.ci.check_profile_archive_boundary import _REVIEWED_DEPENDENCY_ARCHIVES

    repository = Path(__file__).resolve().parents[2]
    relative = next(iter(_REVIEWED_DEPENDENCY_ARCHIVES))
    source = repository / relative
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.symlink_to(source)
    assert _run(tmp_path).returncode == 1
    target.unlink()
    (tmp_path / "copied-source.tar.gz").write_bytes(source.read_bytes())
    assert _run(tmp_path).returncode == 1
