"""A real compression rotation preserves local management and delegated unlock."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.agent.test_compression_logging_session_context import _build_agent_with_db

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.platforms("linux", "macos")
def test_rotated_parent_and_native_child_can_still_manage_the_encrypted_home(tmp_path, monkeypatch):
    from agent.delegation_context import delegated_child_context
    from hermes_cli.config import save_config
    from hermes_security import vault
    from hermes_security.session_authority import ENV_NAME
    from hermes_state import SessionDB
    from tools.delegate_tool import _build_child_agent
    from tools.environments.local import hermes_subprocess_env

    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault.init_vault(home, "synthetic-compression-password")
    vault.unlock(home, "synthetic-compression-password")
    save_config({"agent": {"max_turns": 39}}, strip_defaults=False)
    database = SessionDB(home / "state.db")
    database.create_session("original-owner", source="cli")
    parent = _build_agent_with_db(database, "original-owner")
    delegated = None
    try:
        parent._compress_context([{"role": "user", "content": f"m{i}"} for i in range(20)],
                                 "stable-system", approx_tokens=120_000)
        assert parent.session_id != "original-owner"
        env = hermes_subprocess_env()
        assert ENV_NAME in env, "compression lost the owner's unlock grant"
        delegated = _build_child_agent(task_index=0, goal="Read encrypted configuration", context=None,
                                       toolsets=["terminal"], model=None, max_iterations=3,
                                       task_count=1, parent_agent=parent)
        with delegated_child_context(delegated.session_id):
            env = hermes_subprocess_env()
        script = """
from hermes_cli.management import execute
from hermes_constants import get_hermes_home
from hermes_security.vault import is_unlocked
home = get_hermes_home()
print(execute({'op': 'config.get', 'path': ['agent', 'max_turns']}, home)['value'])
assert not is_unlocked(home), 'management client received a vault key'
"""
        read = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                              stdin=subprocess.DEVNULL, text=True, capture_output=True, timeout=20, check=False)
        assert read.returncode == 0 and read.stdout.strip() == "39", (read.stdout, read.stderr)
        delegated.close()
        delegated = None
        assert ENV_NAME in hermes_subprocess_env()
        assert (home / "config.yaml").read_bytes().startswith(b"HRMVAULT")
        assert json.loads(env[ENV_NAME])["home"] == str(home)
    finally:
        if delegated is not None:
            delegated.close()
        parent.close()
        database.close()
        vault.clear_vault_cache()
