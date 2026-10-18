"""Actual checkout verification refuses a reported pull that left HEAD pinned."""
import subprocess

import pytest

from hermes_cli import main, update_cmd


def test_update_fails_loudly_when_head_pinned(monkeypatch, tmp_path, capsys):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, capture_output=True,
                              text=True, check=True).stdout.strip()

    git("init", "-q", "-b", "main")
    git("config", "user.name", "Native verification")
    git("config", "user.email", "native@example.invalid")
    (tmp_path / "tracked").write_text("old")
    git("add", "tracked")
    git("-c", "commit.gpgsign=false", "commit", "-qm", "old")
    pinned = git("rev-parse", "HEAD")
    git("checkout", "-q", "--detach", pinned)
    monkeypatch.setattr(main, "PROJECT_ROOT", tmp_path)
    with pytest.raises(SystemExit) as failure:
        update_cmd._verify_head_after_pull(["git"], "main", pinned,
                                          in_place_update=False, _windows_gateway_resume=None)
    assert failure.value.code == 1
    assert git("rev-parse", "HEAD") == pinned
    out = capsys.readouterr().out
    assert "Code did not move" in out and "✓ Code updated!" not in out
    assert f"git -C {tmp_path} checkout main" in out
