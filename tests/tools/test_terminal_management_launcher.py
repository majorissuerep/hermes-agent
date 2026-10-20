"""The real local terminal preserves its own install command after login PATH resets."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets

import pytest

pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]
ROOT = Path(__file__).resolve().parents[2]


def test_published_management_command_survives_login_path_reset(tmp_path, monkeypatch):
    from hermes_cli._launchers import stage_launcher
    from hermes_cli.config import save_config
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_security import vault
    from hermes_security.session_authority import start_session_authority
    from pm import ensure
    from tests.e2e.vault_home import _fixture_pm_home, prepare_fixture_runtime
    from tools.environments import local

    home = tmp_path / "state"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    token = set_hermes_home_override(home)
    password = secrets.token_urlsafe(32)
    authority = None
    terminal = None
    try:
        vault.init_vault(home, password)
        vault.unlock(home, password)
        save_config({"agent": {"max_turns": 43}}, strip_defaults=False)
        prepare_fixture_runtime(home, ROOT)
        with _fixture_pm_home(home):
            ensure("python", explicit=True)
            active = stage_launcher("hermes", ROOT, tmp_path / "published")
        assert active is not None and active.is_file()
        # Only installation-location discovery is injected; the real published launcher,
        # CLI/bootstrap, login shell, snapshot, local backend and vault grant remain real.
        monkeypatch.setattr(local, "_source_hermes_bin_dir", lambda: str(active.parent), raising=False)
        wrong = tmp_path / "other-install"
        wrong.mkdir()
        marker = tmp_path / "wrong-install-was-run"
        (wrong / "hermes").write_text(f"#!/bin/sh\nprintf wrong > '{marker}'\nexit 91\n")
        (wrong / "hermes").chmod(0o700)
        monkeypatch.setenv("PATH", str(wrong) + os.pathsep + "/usr/bin:/bin")
        # These are real user shell files, sourced by the actual local login bootstrap.
        for name in (".bash_profile", ".bashrc"):
            (tmp_path / name).write_text("export PATH=/usr/bin:/bin\n")
        monkeypatch.setattr(local, "_HERMES_BIN_DIR", local._SENTINEL)
        authority = start_session_authority(home, "terminal-management-launcher")
        assert authority is not None
        monkeypatch.setenv("HERMES_SESSION_ID", "terminal-management-launcher")
        for name, value in authority.environment().items():
            monkeypatch.setenv(name, value)
        terminal = local.LocalEnvironment(cwd=str(tmp_path), timeout=90)
        schema = terminal.execute("hermes manage schema", timeout=90)
        assert schema["returncode"] == 0, schema
        assert "config.batch" in json.loads(schema["output"])["result"]["operations"]
        result = terminal.execute("printf '%s' '{\"op\":\"config.get\",\"path\":[\"agent\",\"max_turns\"]}' | hermes manage apply", timeout=90)
        assert result["returncode"] == 0, result
        assert json.loads(result["output"])["result"]["value"] == 43
        assert not marker.exists(), "terminal selected an unrelated ambient Hermes installation"
    finally:
        if terminal is not None:
            terminal.cleanup()
        if authority is not None:
            authority.close()
        vault.clear_vault_cache()
        reset_hermes_home_override(token)
