"""Public management commands preserve encrypted state across independent owners."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from dataclasses import dataclass, field

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]


@dataclass
class ManagementWorld:
    home: Path
    env: dict[str, str] = field(repr=False)
    password: str = field(repr=False)
    authority: object = field(repr=False)

    def diagnostic(self, value, *, secret=None, env=None):
        text = str(value)
        hidden = {self.password, secret}
        for source in (self.env, env or {}):
            hidden.update(value for name, value in source.items() if any(
                part in name.upper() for part in ("VAULT", "KEY", "TOKEN", "PASSWORD", "SECRET", "AUTHORIZATION")
            ))
        for value in sorted((item for item in hidden if item), key=len, reverse=True):
            text = text.replace(value, "[REDACTED]")
        return text[:8000]

    def call(self, request=None, *, secret=None, env=None, profile=None, global_profile=False):
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
            args = ["-p", profile, "manage"] if global_profile else ["manage"]
            if profile is not None and not global_profile:
                args.extend(["--profile", profile])
            args.append("apply" if data is not None else "schema")
            result = subprocess.run(
                runtime_command(ROOT, args),
                env=env or self.env, cwd=ROOT, input=json.dumps(data) if data is not None else "",
                text=True, capture_output=True, timeout=60, pass_fds=() if fd is None else (fd,),
                check=False,
            )
        finally:
            if fd is not None:
                os.close(fd)
        diagnostic = self.diagnostic(
            {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr},
            secret=secret, env=env,
        )
        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise AssertionError(f"management did not return valid JSON: {diagnostic}") from None
        assert output["version"] == 1, diagnostic
        assert output["ok"] is (result.returncode == 0), diagnostic
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
    from tests.e2e.vault_home import prepare_fixture_cli

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
    directory = prepare_fixture_cli(home, ROOT, tmp_path / "fixture-cli")
    authority = start_session_authority(home, "management-parent")
    assert authority is not None
    env = dict(os.environ, **authority.environment(), HERMES_SESSION_ID="management-parent")
    env["PATH"] = str(directory) + os.pathsep + env.get("PATH", "")
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










