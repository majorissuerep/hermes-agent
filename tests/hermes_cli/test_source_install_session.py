"""Source-install stages share only temporary, home-scoped unlock authority."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hermes_security import vault


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("exit_code", [0, 7])
def test_install_stages_reuse_unlock_and_revoke_it_on_success_or_failure(tmp_path, monkeypatch, exit_code):
    from hermes_cli.config import read_raw_config, save_config
    from hermes_cli.source_install_session import run_install_session
    from hermes_security import session_authority

    home = tmp_path / "encrypted-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_ALLOW_NO_VAULT", raising=False)
    vault.init_vault(home, "synthetic-install-password")
    vault.unlock(home, "synthetic-install-password")
    save_config({"agent": {"max_turns": 19}}, merge_existing=False)
    observed = []
    start = session_authority.start_session_authority

    def capture(*args):
        authority = start(*args)
        observed.append(authority)
        return authority

    monkeypatch.setattr(session_authority, "start_session_authority", capture)
    child = """
import json, os, subprocess, sys
from hermes_cli.update_vault import adopt_keys
adopt_keys()
from hermes_cli.config import read_raw_config, save_config
assert read_raw_config()['agent']['max_turns'] == 19
save_config({'agent': {'max_turns': 29}}, merge_existing=True)
assert 'HERMES_MASTER_PASSWORD' not in os.environ
result = subprocess.run([sys.executable, '-m', 'hermes_cli.main', 'config', 'get', 'agent.max_turns'],
                        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False)
assert result.returncode == 0, result.stderr
assert '29' in result.stdout
raise SystemExit(int(sys.argv[1]))
"""
    try:
        assert run_install_session([sys.executable, "-c", child, str(exit_code)]) == exit_code
        assert read_raw_config()["agent"]["max_turns"] == 29
        assert (home / "config.yaml").read_bytes().startswith(b"HRMVAULT\x00")
        assert len(observed) == 1
        authority = observed[0]
        assert session_authority.session_environment(home, authority.session_id) == {}
        env = {**os.environ, **authority.environment()}
        independent = subprocess.run(
            [sys.executable, "-m", "hermes_cli.main", "config", "get", "agent.max_turns"],
            env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False,
            cwd=Path(__file__).resolve().parents[2],
        )
        assert independent.returncode != 0
        assert "session unlock is unavailable" in (independent.stdout + independent.stderr).casefold()
    finally:
        vault.lock_now(home)
