"""The shared verification plan fails open when the changed consumers are unknown."""
import subprocess

import pytest

from scripts.ci.verify import plan


@pytest.mark.parametrize("paths", [[".woodpecker/verify.yml"], ["scripts/ci/verify.py"], [], [".github/workflows/ci.yaml"]])
def test_unknown_or_ci_changes_select_every_consumer(paths):
    assert all(plan(paths, "pull_request").values())


def test_known_frontend_change_selects_its_consumers_and_main_checks_everything():
    paths = ["ui-tui/src/app.tsx"]
    assert plan(paths, "pull_request") == {"python": False, "frontend": True, "site": False}
    assert all(plan(paths, "push").values())


def test_verification_refuses_dirty_or_replaced_git_inputs(tmp_path, monkeypatch):
    from scripts.ci import verify

    monkeypatch.setattr(verify, "ROOT", tmp_path)
    for arguments in (("init", "-q"), ("config", "user.name", "fixture"),
                      ("config", "user.email", "fixture@example.invalid"),
                      ("commit", "--allow-empty", "-qm", "initial")):
        subprocess.run(["git", *arguments], cwd=tmp_path, check=True, timeout=10)
    head = verify.git("rev-parse", "HEAD")
    verify.require_clean_checkout(head)
    (tmp_path / "pending.txt").write_text("uncommitted operator work", encoding="utf-8")
    with pytest.raises(SystemExit, match="clean committed checkout"):
        verify.require_clean_checkout(head)
    assert (tmp_path / "pending.txt").read_text(encoding="utf-8") == "uncommitted operator work"
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, timeout=10)
    subprocess.run(["git", "commit", "-qm", "changed"], cwd=tmp_path, check=True, timeout=10)
    with pytest.raises(SystemExit, match="revision changed"):
        verify.require_clean_checkout(head)
