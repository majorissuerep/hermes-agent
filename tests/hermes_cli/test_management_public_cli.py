"""Public management commands preserve encrypted state across independent owners."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]


@dataclass
class ManagementWorld:
    home: Path
    env: dict[str, str]
    password: str
    authority: object

    def call(self, request=None, *, secret=None, env=None):
        from hermes_cli._launchers import runtime_command

        data = dict(request) if request is not None else None
        fd = None
        if secret is not None:
            fd, writer = os.pipe()
            try:
                os.write(writer, secret.encode("utf-8"))
            finally:
                os.close(writer)
            data["secret_fd"] = fd
        try:
            result = subprocess.run(
                runtime_command(ROOT, ["manage", "apply" if data is not None else "schema"]),
                env=env or self.env, cwd=ROOT, input=json.dumps(data) if data is not None else "",
                text=True, capture_output=True, timeout=60, pass_fds=() if fd is None else (fd,),
                check=False,
            )
        finally:
            if fd is not None:
                os.close(fd)
        output = json.loads(result.stdout)
        assert output["version"] == 1, output
        assert output["ok"] is (result.returncode == 0), (output, result.stderr)
        return result, output

    def get(self, path=()):
        result, output = self.call({"op": "config.get", "path": list(path)})
        assert result.returncode == 0, (output, result.stderr)
        return output["result"]


@pytest.fixture
def world(tmp_path, monkeypatch):
    from hermes_cli.config import save_config
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_security import vault
    from hermes_security.session_authority import start_session_authority
    from hermes_state import SessionDB
    from tests.e2e.vault_home import prepare_fixture_runtime

    home = tmp_path / "state"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    token = set_hermes_home_override(home)
    password = secrets.token_urlsafe(32)
    vault.init_vault(home, password)
    owner = vault.unlock(home, password)
    save_config({"agent": {"max_turns": 19}, "updates": {"check": False}}, strip_defaults=False)
    text = owner.read_bytes(home / "config.yaml", purpose="config")
    owner.write_bytes(home / "config.yaml", b"# Keep this operator comment.\n" + text, purpose="config")
    db = SessionDB(home / "state.db")
    db.create_session("management-history", "cli")
    db.append_message("management-history", "user", "preserve this encrypted conversation")
    db.close()
    prepare_fixture_runtime(home, ROOT)
    authority = start_session_authority(home, "management-parent")
    assert authority is not None
    env = dict(os.environ, **authority.environment(), HERMES_SESSION_ID="management-parent")
    for key in ("HERMES_ALLOW_NO_VAULT", "HERMES_VAULT_PRIVATE_KEY", "HERMES_VAULT_KEY_FD",
                "HERMES_MASTER_PASSWORD", "HERMES_PYTHON"):
        env.pop(key, None)
    try:
        yield ManagementWorld(home, env, password, authority)
    finally:
        authority.close()
        vault.clear_vault_cache()
        reset_hermes_home_override(token)


def _fresh_unlock(world, credential, *, private_key=False):
    code = """
import json, os, sys
from pathlib import Path
from hermes_security import vault
home = Path(os.environ['HERMES_HOME'])
value = sys.stdin.buffer.read()
if sys.argv[1] == 'key':
    owner = vault.unlock_with_private_key(home, value)
else:
    owner = vault.unlock(home, value)
print(json.dumps({'config_readable': bool(owner.read_bytes(home / 'config.yaml', purpose='config'))}))
"""
    env = dict(world.env)
    env.pop("HERMES_SESSION_VAULT", None)
    data = credential if isinstance(credential, bytes) else credential.encode("utf-8")
    return subprocess.run([sys.executable, "-c", code, "key" if private_key else "password"],
                          input=data, cwd=ROOT, env=env, capture_output=True, timeout=30, check=False)


def test_public_management_rotation_keeps_live_children_and_ciphertext(world):
    from hermes_security import vault
    from hermes_security.session_authority import start_session_authority
    from hermes_state import SessionDB

    before = world.get()
    result, output = world.call({"op": "config.batch", "revision": before["revision"], "operations": [
        {"op": "config.set", "path": ["agent", "max_turns"], "value": 37},
        {"op": "config.set", "path": ["skills", "config", "literal.key"],
         "value": {"enabled": True, "values": [1, None, "kept"]}},
    ]})
    assert result.returncode == 0, (output, result.stderr)
    assert world.get(["agent", "max_turns"])["value"] == 37
    assert world.get(["skills", "config", "literal.key"])["value"]["values"] == [1, None, "kept"]
    secret = "synthetic-private-header-" + secrets.token_hex(16)
    result, output = world.call({"op": "secret.set", "name": "MANAGEMENT_TEST_KEY"}, secret=secret)
    assert result.returncode == 0, (output, result.stderr)
    assert secret not in result.stdout + result.stderr
    revision = world.get()["revision"]
    result, output = world.call({"op": "config.set", "revision": revision,
        "path": ["providers", "custom.route", "extra_headers", "Authorization"],
        "value": "Bearer ${MANAGEMENT_TEST_KEY}"})
    assert result.returncode == 0, (output, result.stderr)
    assert secret not in json.dumps(world.get())
    private, public = vault.generate_keypair()
    vault.add_key_slot(world.home, public_key_raw=public, unlocked_vault=vault.get_vault(world.home))
    unchanged = {name: (world.home / name).read_bytes() for name in ("config.yaml", ".env", "state.db")}
    new_password = secrets.token_urlsafe(32)
    result, output = world.call({"op": "passphrase.change"}, secret=new_password)
    assert result.returncode == 0, (output, result.stderr)
    assert new_password not in result.stdout + result.stderr
    assert unchanged == {name: (world.home / name).read_bytes() for name in unchanged}
    assert world.get(["agent", "max_turns"])["value"] == 37
    child = start_session_authority(world.home, "management-child")
    try:
        child_env = dict(world.env, **child.environment(), HERMES_SESSION_ID="management-child")
        result, output = world.call({"op": "config.get", "path": ["agent", "max_turns"]}, env=child_env)
        assert result.returncode == 0 and output["result"]["value"] == 37
    finally:
        child.close()
    assert world.get(["agent", "max_turns"])["value"] == 37
    assert _fresh_unlock(world, world.password).returncode != 0
    assert _fresh_unlock(world, new_password).returncode == 0
    assert _fresh_unlock(world, private, private_key=True).returncode == 0
    with SessionDB(world.home / "state.db") as db:
        assert db.get_messages("management-history")[0]["content"] == "preserve this encrypted conversation"
    text = vault.get_vault(world.home).read_bytes(world.home / "config.yaml", purpose="config")
    assert b"# Keep this operator comment." in text
    assert (world.home.stat().st_mode & 0o777) == 0o700
    for name in unchanged:
        assert (world.home / name).stat().st_mode & 0o077 == 0
        assert secret.encode() not in (world.home / name).read_bytes()
    world.authority.close()
    result, output = world.call({"op": "config.get", "path": []})
    assert result.returncode != 0 and output["ok"] is False


def test_public_management_cas_and_damaged_state_fail_closed(world, tmp_path):
    revision = world.get()["revision"]
    requests = [{"op": "config.set", "path": ["skills", "config", key], "value": key,
                 "revision": revision} for key in ("first", "second")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(world.call, requests))
    assert sorted(result.returncode == 0 for result, _ in results) == [False, True]
    value = world.get(["skills", "config"])["value"]
    assert len(value) == 1 and next(iter(value)) in {"first", "second"}
    original = (world.home / "config.yaml").read_bytes()
    result, output = world.call({"op": "config.batch", "revision": world.get()["revision"], "operations": [
        {"op": "config.set", "path": ["agent", "max_turns"], "value": 100},
        {"op": "config.set", "path": [], "value": []},
    ]})
    assert result.returncode != 0 and output["ok"] is False
    assert (world.home / "config.yaml").read_bytes() == original
    other = tmp_path / "other"
    from hermes_security import vault

    vault.init_vault(other, secrets.token_urlsafe(32))
    result, output = world.call({"op": "config.get", "path": []}, env=dict(world.env, HERMES_HOME=str(other)))
    assert result.returncode != 0 and output["ok"] is False
    damaged = original[:-1] + bytes([original[-1] ^ 1])
    (world.home / "config.yaml").write_bytes(damaged)
    result, output = world.call({"op": "config.set", "path": ["agent", "max_turns"],
                               "value": 100, "revision": revision})
    assert result.returncode != 0 and output["ok"] is False
    assert (world.home / "config.yaml").read_bytes() == damaged


def test_public_management_schema_needs_no_vault(tmp_path, monkeypatch):
    from hermes_cli._launchers import runtime_command

    home = tmp_path / "uninitialized"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    env = dict(os.environ)
    for key in ("HERMES_ALLOW_NO_VAULT", "HERMES_VAULT_PRIVATE_KEY", "HERMES_VAULT_KEY_FD",
                "HERMES_SESSION_VAULT", "HERMES_MASTER_PASSWORD", "HERMES_PYTHON"):
        env.pop(key, None)
    result = subprocess.run(runtime_command(ROOT, ["manage", "schema"]), cwd=ROOT, env=env,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60, check=False)
    output = json.loads(result.stdout)
    assert result.returncode == 0 and output["version"] == 1 and output["ok"] is True
    assert not (home / ".hermes-vault").exists()
