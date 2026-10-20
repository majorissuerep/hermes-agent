"""Model file edits cannot bypass canonical encrypted management writers."""
import json
import os
from pathlib import Path

import pytest


@pytest.mark.parametrize("name", ["config.yaml", "config.yaml.lock", ".env", ".hermes-vault", ".hermes-vault.lock",
                                   "state.db", "state.db-wal", "cron/jobs.sqlite3",
                                   "profiles/worker/config.yaml", "profiles/worker/.env"])
def test_write_and_patch_refuse_managed_state_before_backend_access(tmp_path, monkeypatch, name):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from tools import file_tools

    home = tmp_path / "home"
    target = home / name
    target.parent.mkdir(parents=True)
    target.write_bytes(b"opaque original managed bytes")
    token = set_hermes_home_override(home)

    def unavailable(*args):
        raise AssertionError("a refused managed edit reached the execution backend")

    monkeypatch.setattr(file_tools, "_get_file_ops", unavailable)
    try:
        calls = [lambda: file_tools.write_file_tool(str(target), "replacement", cross_profile=True),
                 lambda: file_tools.patch_tool(path=str(target), old_string="original", new_string="replacement")]
        for call in calls:
            result = json.loads(call())
            assert "error" in result, result
            assert "hermes manage" in result["error"], result
            assert target.read_bytes() == b"opaque original managed bytes"
    finally:
        reset_hermes_home_override(token)


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("alias", ["symlink", "hardlink", "parent-symlink"])
def test_aliases_cannot_evade_guard_and_workspace_source_remains_editable(tmp_path, alias):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from tools.file_tools_write_guards import _check_sensitive_path

    home = tmp_path / "home"
    home.mkdir()
    target = home / ".env"
    target.write_text("PRIVATE=value")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    entry = workspace / "innocent.txt"
    if alias == "symlink":
        entry.symlink_to(target)
    elif alias == "hardlink":
        os.link(target, entry)
    else:
        entry.symlink_to(home, target_is_directory=True)
        entry = entry / ".env"
    token = set_hermes_home_override(home)
    try:
        assert "hermes manage" in _check_sensitive_path(str(entry))
        assert _check_sensitive_path(str(workspace / "source.py")) is None
        assert _check_sensitive_path(str(home / "skills" / "mine" / "config.yaml")) is None
        assert _check_sensitive_path(str(home / "hermes-agent" / "config.yaml")) is None
    finally:
        reset_hermes_home_override(token)


def test_canonical_managed_writer_is_not_blocked_by_model_tool_policy(tmp_path):
    from hermes_security import io, vault
    from tools.file_tools_write_guards import _check_sensitive_path

    home = tmp_path / "owner"
    home.mkdir()
    vault.init_vault(home, "synthetic management password")
    vault.unlock(home, "synthetic management password")
    try:
        path = home / ".env"
        io.write_text(path, "TOKEN=first\n", purpose="env")
        assert "hermes manage" in _check_sensitive_path(str(path))
        before = path.read_bytes()
        io.write_text(path, "TOKEN=second\n", purpose="env")
        assert path.read_bytes() != before
        assert b"TOKEN" not in path.read_bytes()
        assert io.read_text(path, purpose="env") == "TOKEN=second\n"
        assert Path(path).stat().st_mode & 0o777 == 0o600
    finally:
        vault.lock_now(home)
