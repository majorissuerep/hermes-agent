"""Native source-update debt and Git commit-point custody.

Actual local completion markers retain prior bytes on failed moves and cannot be
redirected by predictable aliases. Real Git races must land on the armed object,
not an independently moving branch/ref. No service restart is requested.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from hermes_cli import update_cmd_commit as commit
from hermes_cli.venv_sync import completion_pending_path


@pytest.fixture(autouse=True)
def _fresh_commit_point(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    commit.begin_update_attempt()
    yield
    commit.begin_update_attempt()


@pytest.fixture
def root(tmp_path) -> Path:
    install = tmp_path / "install"
    install.mkdir()
    return install






def test_plain_arm_and_disarm_restores_what_the_run_found(root):
    host = completion_pending_path(root)
    host.parent.mkdir(parents=True, exist_ok=True)
    host.write_text("OLD", encoding="utf-8")
    commit.arm_commit_obligations(root, "a" * 40)
    assert host.read_text(encoding="utf-8-sig") != "OLD"

    commit.disarm_commit_obligations()

    assert host.read_text(encoding="utf-8-sig") == "OLD"


def test_an_unreadable_record_refuses_the_arm_and_survives(root, monkeypatch):
    """A read error is not absence: guessing 'none' would delete the record on disarm (F28)."""
    host = completion_pending_path(root)
    host.parent.mkdir(parents=True, exist_ok=True)
    host.write_text("OLD", encoding="utf-8")
    real = Path.read_bytes

    def flaky(self):
        if self == host:
            raise PermissionError(13, "sharing violation", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", flaky)
    with pytest.raises(OSError):
        commit.arm_commit_obligations(root, "a" * 40)
    monkeypatch.setattr(Path, "read_bytes", real)
    commit.disarm_commit_obligations()

    assert host.read_text(encoding="utf-8-sig") == "OLD"


@pytest.mark.platforms("posix")  # unprivileged symlinks
def test_disarm_never_writes_through_a_planted_restore_alias(root, tmp_path):
    """A same-user link at a temp name must not redirect the restore (N05)."""
    host = completion_pending_path(root)
    host.parent.mkdir(parents=True, exist_ok=True)
    host.write_text("ORIGINAL", encoding="utf-8")
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("PRECIOUS", encoding="utf-8")
    commit.arm_commit_obligations(root, "a" * 40)
    for alias in (host.with_name(host.name + ".restore"), host.with_name(f".{host.name}.{os.getpid()}.restore")):
        alias.symlink_to(sentinel)

    commit.disarm_commit_obligations()

    assert sentinel.read_text(encoding="utf-8-sig") == "PRECIOUS"
    assert not host.is_symlink() and host.read_text(encoding="utf-8-sig") == "ORIGINAL"


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          encoding="utf-8", env=env).stdout.strip()










def _abc_clone(tmp_path, monkeypatch):
    """Upstream commits A, B, C; a clone of it with PROJECT_ROOT pointed at it. Returns (clone, a, b, c)."""
    import hermes_cli.main as hermes_main

    up, clone = tmp_path / "up", tmp_path / "clone"
    up.mkdir()
    _git(up, "init", "-q", "-b", "main")
    shas = []
    for name in "ABC":
        (up / "f").write_text(name, encoding="utf-8")
        _git(up, "add", "-A")
        _git(up, "commit", "-qm", name)
        shas.append(_git(up, "rev-parse", "HEAD"))
    _git(tmp_path, "clone", "-q", str(up), str(clone))
    monkeypatch.setattr(hermes_main, "PROJECT_ROOT", clone)
    monkeypatch.setattr(commit, "_owns_live_checkout", lambda _root: False)
    monkeypatch.chdir(clone)
    return (clone, *shas)


def _move_ref_after_arm(monkeypatch, clone, ref, sha):
    """The race: ``ref`` moves to ``sha`` right after the commit point armed (marker + debt)."""
    real = commit.arm_commit_point

    def arm_then_move(*args, **kwargs):
        refused = real(*args, **kwargs)
        _git(clone, "update-ref", ref, sha)
        return refused

    monkeypatch.setattr(commit, "arm_commit_point", arm_then_move)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_the_pull_merges_the_armed_commit_not_a_tracking_ref_that_moved(tmp_path, monkeypatch):
    """CP1 armed B, then origin/main moved to C before ``merge --ff-only origin/main``: HEAD landed C
    while the debt named B and the marker was gone (review O3). The merge names the armed OID."""
    from hermes_cli import update_cmd
    from hermes_cli._early_recovery import interrupted_pull_marker

    clone, a, b, c = _abc_clone(tmp_path, monkeypatch)
    _git(clone, "reset", "-q", "--hard", a)
    _git(clone, "update-ref", "refs/remotes/origin/main", b)
    commit.record_run_start(["git"], clone)
    _move_ref_after_arm(monkeypatch, clone, "refs/remotes/origin/main", c)
    update_cmd._pull_updates(["git"], "main", None, prompt_for_restore=False, gw_input_fn=None,
                             discard_local_changes=False, keep_stash=False)
    assert _git(clone, "rev-parse", "HEAD") == b
    assert completion_pending_path(clone).is_file()
    assert not interrupted_pull_marker(clone).exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_branch_that_moves_during_the_switch_leaves_head_and_debt_agreeing(tmp_path, monkeypatch):
    """CP0 resolved main to A and armed A, then main moved to B before ``checkout main``: the switch
    landed B, reported success and dropped the marker while the debt named A (review O2). It must
    refuse, with the debt following the HEAD it landed on."""
    from hermes_cli import update_cmd
    from hermes_cli._early_recovery import interrupted_pull_marker

    clone, a, b, _c = _abc_clone(tmp_path, monkeypatch)
    _git(clone, "reset", "-q", "--hard", a)
    _git(clone, "checkout", "-q", "-b", "feat")
    (clone / "g").write_text("X", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "X")
    x = _git(clone, "rev-parse", "HEAD")
    commit.record_run_start(["git"], clone)
    _move_ref_after_arm(monkeypatch, clone, "refs/heads/main", b)
    switched = update_cmd._switch_branch_at_commit_point(["git"], "main", "origin/main", pre=x, stash=None)
    assert switched.returncode == 1 and "moved" in switched.stderr
    assert _git(clone, "rev-parse", "HEAD") == b
    assert completion_pending_path(clone).is_file()
    assert not interrupted_pull_marker(clone).exists()












@pytest.mark.platforms("posix")  # unprivileged symlinks
@pytest.mark.parametrize("kind", ["regular", "symlink", "hardlink", "none"])
def test_disarm_restores_through_an_unpredictable_temp_and_leaves_every_sibling(root, tmp_path, kind):
    """The restore used a fixed ``.<name>.<pid>.restore`` temp and unlinked whatever stood there
    first: a user's ordinary file of that name was deleted (review O1). Nothing at the old name, or
    any alias target, is touched; the record still comes back."""
    from hermes_cli.venv_sync import completion_pending_path

    tail = completion_pending_path(root)
    tail.parent.mkdir(parents=True, exist_ok=True)
    tail.write_bytes(b"OLD-TAIL")
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"PRECIOUS")
    commit.arm_commit_obligations(root, "a" * 40)
    planted = tail.with_name(f".{tail.name}.{os.getpid()}.restore")
    if kind == "regular":
        planted.write_bytes(b"USER-FILE")
    elif kind == "symlink":
        planted.symlink_to(sentinel)
    elif kind == "hardlink":
        os.link(sentinel, planted)

    commit.disarm_commit_obligations()

    assert tail.read_bytes() == b"OLD-TAIL" and not tail.is_symlink()
    assert sentinel.read_bytes() == b"PRECIOUS"
    if kind == "regular":
        assert planted.read_bytes() == b"USER-FILE"
    elif kind != "none":
        assert os.path.lexists(planted) and planted.read_bytes() == b"PRECIOUS"
    assert sorted(p.name for p in tail.parent.iterdir() if p.name.endswith(".restore")) == (
        [planted.name] if kind != "none" else [])
