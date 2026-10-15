"""The fork updater never imports or publishes another repository's commits."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from hermes_cli.update_cmd_git import _sync_with_upstream_if_needed


def _env(home: Path) -> dict:
    return {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(home),
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          encoding="utf-8", env=_env(cwd)).stdout.strip()


def _fork_behind_upstream(tmp_path, monkeypatch):
    """A fork clone at c1 (origin/main) whose fetched upstream/main is c2; returns (clone, commits)."""
    for name, value in _env(tmp_path).items():
        if name.startswith("GIT_") or name == "HOME":
            monkeypatch.setenv(name, value)
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "-q", "-b", "main")
    commits = []
    for i in range(3):
        (upstream / "f.txt").write_text(f"{i}\n", encoding="utf-8")
        _git(upstream, "add", "f.txt")
        _git(upstream, "commit", "-qm", f"c{i}")
        commits.append(_git(upstream, "rev-parse", "HEAD"))
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(upstream), str(origin))
    _git(origin, "update-ref", "refs/heads/main", commits[1])
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    _git(clone, "remote", "add", "upstream", str(upstream))
    return clone, commits


@pytest.mark.parametrize("assume_yes", [False, True])
def test_updater_leaves_upstream_commits_origin_and_user_tree_untouched(tmp_path, monkeypatch, assume_yes):
    clone, commits = _fork_behind_upstream(tmp_path, monkeypatch)
    _git(clone, "branch", "upstream/main", commits[0])
    (clone / "local-notes.txt").write_text("keep my untracked work")
    before = {"head": _git(clone, "rev-parse", "HEAD"),
              "refs": _git(clone, "show-ref"),
              "origin": _git(tmp_path / "origin.git", "show-ref"),
              "tree": {p.name: p.read_bytes() for p in clone.iterdir() if p.is_file()}}
    def no_prompt(*args):
        raise AssertionError("updating a fork must not prompt to import upstream")
    assert _sync_with_upstream_if_needed(["git"], clone, assume_yes=assume_yes, input_fn=no_prompt) is False
    assert _git(clone, "rev-parse", "HEAD") == before["head"] == commits[1]
    assert _git(clone, "show-ref") == before["refs"]
    assert _git(tmp_path / "origin.git", "show-ref") == before["origin"]
    assert {p.name: p.read_bytes() for p in clone.iterdir() if p.is_file()} == before["tree"]
