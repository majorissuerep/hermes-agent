"""Real subprocess handoffs remain scoped, encrypted and revocable without a TTY."""
import json
import os
from pathlib import Path
from contextlib import ExitStack
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
    return subprocess.run([sys.executable, "-c", script, action], check=False, cwd=ROOT, env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("nested_profile", [False, True])
def test_session_and_delegated_children_reuse_unlock_and_keep_state_encrypted(tmp_path, monkeypatch, nested_profile):
    from hermes_security.session_authority import start_session_authority
    from hermes_cli.config import save_config, read_raw_config

    owner = tmp_path / "home"
    home = owner / "profiles" / "work" if nested_profile else owner
    password = "synthetic-session-password"
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault.init_vault(owner, password)
    vault.unlock(owner, password)
    home.mkdir(parents=True, exist_ok=True)
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
        locked_grant = grants[0].environment()
        vault.lock_now(homes[0])
        vault.unlock(homes[0], "synthetic-independent-password")
        assert child(homes[0], locked_grant).returncode != 0, "reunlock revived a revoked session grant"
        assert child(homes[1], grants[1].environment()).returncode == 0
        fresh = start_session_authority(homes[0], "fresh-owner")
        try:
            assert child(homes[0], fresh.environment()).returncode == 0
        finally:
            fresh.close()
        expired = grants[0].environment()
        grants[0].close()
        assert child(homes[0], expired).returncode != 0
        assert child(homes[1], grants[1].environment()).returncode == 0
    finally:
        for grant in grants:
            grant.close()
        vault.clear_vault_cache()


@pytest.mark.platforms("posix")
def test_derived_grants_end_with_the_owner_but_child_close_keeps_parent(tmp_path, monkeypatch):
    from hermes_security.session_authority import start_session_authority
    from hermes_cli.config import save_config

    home = tmp_path / "state"
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault.init_vault(home, "synthetic-lineage-password")
    vault.unlock(home, "synthetic-lineage-password")
    save_config({"agent": {"max_turns": 29}}, merge_existing=True)
    owner = start_session_authority(home, "owner")
    delegated = start_session_authority(home, "delegated", parent_session_id="owner")
    sibling = start_session_authority(home, "sibling", parent_session_id="owner")
    try:
        assert child(home, delegated.environment()).returncode == 0
        delegated.close()
        assert child(home, owner.environment()).returncode == 0
        assert child(home, sibling.environment()).returncode == 0
        owner.close()
        assert child(home, sibling.environment()).returncode != 0
    finally:
        for authority in (delegated, sibling, owner):
            authority.close()
        vault.clear_vault_cache()


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("key_handoff", [False, True])
def test_nested_hermes_cannot_renew_its_grant_past_the_original_owner(tmp_path, monkeypatch, key_handoff):
    from hermes_security.session_authority import start_session_authority
    from hermes_cli.config import save_config

    home = tmp_path / "state"
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault.init_vault(home, "synthetic-nested-password")
    vault.unlock(home, "synthetic-nested-password")
    save_config({"agent": {"max_turns": 31}}, merge_existing=True)
    owner = start_session_authority(home, "owner")
    script = """
import json, sys
from hermes_security.handoff import adopt_inherited_key
from hermes_security.session_authority import start_session_authority
from hermes_constants import get_hermes_home
assert adopt_inherited_key()
nested = start_session_authority(get_hermes_home(), 'nested')
try:
    print(json.dumps(nested.environment()), flush=True)
    sys.stdin.read(1)
finally:
    nested.close()
"""
    env = dict(os.environ, **owner.environment())
    env.pop("HERMES_VAULT_KEY_FD", None)
    with ExitStack() as resources:
        descriptors = ()
        if key_handoff:
            from hermes_security.handoff import prepare_child_key
            fd, additions = prepare_child_key(home)
            resources.callback(os.close, fd)
            env.update(additions)
            descriptors = (fd,)
        process = resources.enter_context(subprocess.Popen(
            [sys.executable, "-c", script], env=env, cwd=ROOT, pass_fds=descriptors,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        try:
            import select
            assert select.select([process.stdout], [], [], 15)[0], "nested owner failed to start"
            grant = json.loads(process.stdout.readline())
            assert child(home, grant).returncode == 0
            owner.close()
            assert child(home, grant).returncode != 0
        finally:
            process.communicate("x", timeout=20)
            owner.close()
            vault.clear_vault_cache()
