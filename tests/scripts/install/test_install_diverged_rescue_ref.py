"""Regression: the installer's update reset must not drop local commits unanchored.

Re-running ``install.sh`` over an existing checkout (desktop
bootstrap and its update retry do this) falls back to
``reset --hard origin/<branch>`` when a fast-forward fails. Commits made on that
branch must survive behind a ``refs/hermes-update-backups/`` rescue ref, the same
namespace ``hermes update`` uses, and the installer must print the ref.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(f"{name}\n", encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"add {name}")
    return _git(repo, "rev-parse", "HEAD")


def _diverged_managed_checkout(tmp_path: Path) -> tuple[Path, str]:
    """A managed checkout on ``main`` with one local commit, while its origin moved on."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "-q", "-b", "main")
    _commit(upstream, "shared.txt")
    managed = tmp_path / "hermes-agent"
    _git(tmp_path, "clone", "-q", str(upstream), str(managed))
    local_sha = _commit(managed, "local-fix.txt")
    _commit(upstream, "upstream-only.txt")
    return managed, local_sha


def _assert_local_commit_parked(repo: Path, local_sha: str, output: str) -> None:
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "origin/main")
    refs = dict(
        line.split()[::-1] for line in _git(
            repo, "for-each-ref", "--format=%(refname) %(objectname)",
            "refs/hermes-update-backups/").splitlines())
    ref = refs.get(local_sha)
    assert ref and ref.startswith("refs/hermes-update-backups/diverged-main-"), refs
    assert ref in output, "the installer must print where the commits went"
    assert _git(repo, "log", "--format=%H", f"origin/main..{ref}").split() == [local_sha]


@pytest.mark.live_system_guard_bypass
@pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None,
    reason="needs git and bash",
)
def test_install_sh_repository_stage_parks_local_commits_before_reset(tmp_path: Path) -> None:
    managed, local_sha = _diverged_managed_checkout(tmp_path)
    env = os.environ | {
        "HERMES_HOME": str(tmp_path / "hermes-home"),
        "HERMES_INSTALL_DIR": str(managed),
    }

    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--stage", "repository", "--non-interactive"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    _assert_local_commit_parked(managed, local_sha, result.stdout + result.stderr)
