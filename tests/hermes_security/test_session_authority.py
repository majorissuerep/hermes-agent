"""Real subprocess handoffs remain scoped, encrypted and revocable without a TTY."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hermes_security import vault

ROOT = Path(__file__).resolve().parents[2]


def child(home, grant, action="read"):
    env = dict(os.environ, HERMES_HOME=str(home), **grant)
    for name in ("HERMES_ALLOW_NO_VAULT", "HERMES_VAULT_PRIVATE_KEY", "HERMES_VAULT_KEY_FD", "HERMES_MASTER_PASSWORD"):
        env.pop(name, None)
    script = """
from types import SimpleNamespace
from hermes_cli.vault_gate import gate_startup
from hermes_cli.config import read_raw_config, save_config
gate_startup(SimpleNamespace(command='config'))
import json, sys
if sys.argv[1] == 'write':
    save_config({'agent': {'max_turns': 73}}, merge_existing=True)
print(json.dumps(read_raw_config()))
"""
    return subprocess.run([sys.executable, "-c", script, action], cwd=ROOT, env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)


@pytest.mark.platforms("posix")
def test_session_and_delegated_children_reuse_unlock_and_keep_state_encrypted(tmp_path, monkeypatch):
    from hermes_security.session_authority import start_session_authority
    from hermes_cli.config import save_config, read_raw_config

    home = tmp_path / "home"
    password = "synthetic-session-password"
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault.init_vault(home, password)
    vault.unlock(home, password)
    save_config({'agent': {'max_turns': 19}}, merge_existing=True)
    parent = start_session_authority(home, "parent")
    try:
        result = child(home, parent.environment(), "write")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)['agent']['max_turns'] == 73
        assert read_raw_config()['agent']['max_turns'] == 73
        assert (home / "config.yaml").read_bytes().startswith(b"HRMVAULT")
        assert password not in result.stdout + result.stderr + str(parent.environment())
        delegated = start_session_authority(home, "delegated")
        try:
            assert child(home, delegated.environment()).returncode == 0
        finally:
            delegated.close()
        assert child(home, parent.environment()).returncode == 0
    finally:
        parent.close()
        vault.clear_vault_cache()


@pytest.mark.platforms("posix")
def test_foreign_invalid_and_closed_grants_cannot_unlock_another_home(tmp_path, monkeypatch):
    from hermes_security.session_authority import ENV_NAME, start_session_authority
    from hermes_cli.config import save_config

    homes = [tmp_path / name for name in ("A", "B")]
    grants = []
    try:
        for home in homes:
            monkeypatch.setenv("HERMES_HOME", str(home))
            vault.init_vault(home, "synthetic-independent-password")
            vault.unlock(home, "synthetic-independent-password")
            save_config({'model': {'default': home.name}}, merge_existing=True)
            grants.append(start_session_authority(home, home.name))
        for home, grant in ((homes[0], grants[0]), (homes[1], grants[1]), (homes[0], grants[0])):
            result = child(home, grant.environment())
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout)['model']['default'] == home.name
        assert child(homes[1], grants[0].environment()).returncode != 0
        invalid = json.loads(grants[0].environment()[ENV_NAME])
        invalid['token'] = 'not-the-session-token'
        assert child(homes[0], {ENV_NAME: json.dumps(invalid)}).returncode != 0
        expired = grants[0].environment()
        grants[0].close()
        assert child(homes[0], expired).returncode != 0
        assert child(homes[1], grants[1].environment()).returncode == 0
    finally:
        for grant in grants:
            grant.close()
        vault.clear_vault_cache()
