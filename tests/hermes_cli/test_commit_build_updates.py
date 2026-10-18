"""A one-commit artifact cannot enter any self-update path."""

import json
from unittest.mock import Mock

import pytest

from hermes_cli.update_contract import evaluate_update_admission

MESSAGE = "This build doesn't get updates. Ask the developer who gave it to you for a new build."


@pytest.fixture
def commit_build(tmp_path, monkeypatch):
    root = tmp_path / "app"
    root.mkdir()
    (root / "install-stamp.json").write_text(json.dumps({
        "source": "commit-build", "distribution": "desktop-app", "payload": "bundled",
        "updateMechanism": "external", "commit": "a" * 40,
        "displayVersion": "1.2.3+gabcdef12", "baseVersion": "1.2.3", "channel": "canary",
    }))
    monkeypatch.setattr("hermes_cli.image_provenance.IMAGE_PROVENANCE_PATH", tmp_path / "absent")
    monkeypatch.setattr("hermes_cli.config.get_project_root", lambda: root)
    monkeypatch.setattr("pm.paths.repo_root", lambda: root)
    monkeypatch.delenv("HERMES_INSTALL_ROOT", raising=False)
    from hermes_cli.version_info import _reset_version_info_cache
    _reset_version_info_cache()
    yield root
    _reset_version_info_cache()


@pytest.mark.parametrize("git_present", [False, True])
def test_commit_build_refuses_without_gui_advice(commit_build, git_present):
    if git_present:
        (commit_build / ".git").mkdir()
    refusal = evaluate_update_admission(commit_build)
    assert refusal is not None
    assert refusal.code == "commit-build"
    assert refusal.message == MESSAGE
    assert refusal.update_command == ""


@pytest.mark.parametrize("passive", [False, True])
def test_commit_build_never_checks_upstream_or_reuses_source_cache(commit_build, monkeypatch, passive):
    from hermes_cli import banner, source_check

    # Even a shared home's source-checkout cache and an embedded SHA cannot turn this into an update.
    monkeypatch.setenv("HERMES_REVISION", "b" * 40)
    check = Mock(side_effect=AssertionError("must not probe"))
    monkeypatch.setattr(source_check, "_branch_tip", check)
    assert source_check.check_for_updates(passive=passive)["behind"] is None
    check.assert_not_called()


def test_commit_version_banner_uses_stamp_not_shared_checkout(commit_build, monkeypatch):
    from hermes_cli import banner, source_check
    from hermes_cli.version_info import get_version_info

    git = Mock(side_effect=AssertionError("version must not inspect another checkout"))
    monkeypatch.setattr(banner, "get_git_banner_state", git)
    label = banner.format_banner_version_label()
    assert "1.2.3+gabcdef12" in label
    assert "commit-build" in label
    assert get_version_info().source == "commit-build"
    git.assert_not_called()
