"""Borrowed (FD/grant) unlocks stay revocable and cannot rotate the owner's passphrase."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys

import pytest

from hermes_security import vault

ROOT = Path(__file__).resolve().parents[2]
_PASSWORD = "synthetic borrowed password"
pytestmark = pytest.mark.platforms("linux", "macos")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    target = tmp_path / "owner"
    target.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(target))
    vault.init_vault(target, _PASSWORD)
    vault.unlock(target, _PASSWORD)
    yield target
    vault.clear_vault_cache()


def _env(home, extra):
    env = {name: value for name, value in os.environ.items()
           if name not in {"HERMES_SESSION_VAULT", "HERMES_VAULT_KEY_FD", "HERMES_VAULT_PRIVATE_KEY"}}
    env.update(extra, HERMES_HOME=str(home))
    return env


_ROTATE = """
import json, sys
sys.path.insert(0, sys.argv[1])
from hermes_security import vault
from hermes_security.errors import VaultError
from hermes_security.handoff import adopt_inherited_key
from hermes_security.passphrase import change_passphrase
home, current = sys.argv[2], sys.argv[3]
assert adopt_inherited_key()
outcome = {}
for label, password in (("borrowed", None), ("wrong", "not the synthetic password"), ("owner", current)):
    try:
        if password is not None:
            vault.unlock(home, password)
        change_passphrase(home, "rotated by " + label)
        outcome[label] = "changed"
    except VaultError as exc:
        outcome[label] = type(exc).__name__
print(json.dumps(outcome))
"""


def test_fd_borrowed_child_needs_an_explicit_owner_unlock_to_rotate(home):
    from hermes_security.handoff import prepare_child_key

    fd, additions = prepare_child_key(home)
    try:
        result = subprocess.run([sys.executable, "-I", "-c", _ROTATE, str(ROOT), str(home), _PASSWORD],
                                env=_env(home, additions), pass_fds=(fd,), capture_output=True,
                                text=True, timeout=60, check=False)
    finally:
        os.close(fd)
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert outcome["borrowed"] != "changed", "an inherited key FD rotated the owner's passphrase"
    assert outcome["wrong"] == "WrongMasterPasswordError"
    assert outcome["owner"] == "changed"
    vault.clear_vault_cache()
    assert vault.unlock(home, "rotated by owner") is not None


_LIVENESS = """
import json, sys
sys.path.insert(0, sys.argv[1])
from hermes_security import vault
from hermes_security.errors import VaultLockedError
from hermes_security.handoff import adopt_inherited_key
home = sys.argv[2]
assert adopt_inherited_key()
held = vault.get_vault(home, allow_env_unlock=False)
def probe():
    state = {"unlocked": vault.is_unlocked(home)}
    try:
        state["held"] = len(held.master_key)
    except VaultLockedError:
        state["held"] = "wiped"
    for name, call in (("get", lambda: vault.get_vault(home, allow_env_unlock=False)),
                       ("export", lambda: vault.export_unlocked_key(home))):
        try:
            call()
            state[name] = "allowed"
        except VaultLockedError:
            state[name] = "locked"
    return state
print(json.dumps(probe()), flush=True)
sys.stdin.readline()
print(json.dumps(probe()), flush=True)
"""


def test_borrowed_fd_key_stops_working_when_the_owning_grant_is_revoked(home):
    from agent.session_context import scoped_current_session_id
    from hermes_security.handoff import prepare_child_key
    from hermes_security.session_authority import start_session_authority

    authority = start_session_authority(home, "custody-owner")
    with scoped_current_session_id(authority.session_id):
        fd, additions = prepare_child_key(home)  # the FD frame carries the owning session's grant
    try:
        process = subprocess.Popen([sys.executable, "-I", "-c", _LIVENESS, str(ROOT), str(home)],
                                   env=_env(home, additions),
                                   pass_fds=(fd,), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
    finally:
        os.close(fd)
    try:
        assert select.select([process.stdout], [], [], 30)[0], "borrowed child did not start"
        live = json.loads(process.stdout.readline())
        authority.close()
        stdout, stderr = process.communicate("revoked\n", timeout=30)
    finally:
        authority.close()
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
    assert process.returncode == 0, stderr
    assert live == {"unlocked": True, "held": 32, "get": "allowed", "export": "allowed"}
    assert json.loads(stdout) == {"unlocked": False, "held": "wiped", "get": "locked", "export": "locked"}
    owner = vault.get_vault(home, allow_env_unlock=False)  # the lender's own custody is untouched
    owner.write_bytes("config.yaml", b"model: synthetic\n", purpose="config")
    assert owner.read_bytes("config.yaml", purpose="config") == b"model: synthetic\n"
