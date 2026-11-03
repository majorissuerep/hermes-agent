"""Ordinary session grants authorize management, never permanent-key export."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hermes_security import vault


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("pipe", [False, True])
def test_session_bearer_cannot_redeem_a_permanent_key(tmp_path, monkeypatch, pipe):
    from hermes_security.session_authority import ENV_NAME, _request_key, start_session_authority

    home = tmp_path / "owner"
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault.init_vault(home, "synthetic-operation-password")
    vault.unlock(home, "synthetic-operation-password")
    authority = start_session_authority(home, "operation-owner", pipe=pipe)
    try:
        grant = json.loads(authority.environment()[ENV_NAME])
        response = _request_key(grant)
        # Reproduce the former export without ever displaying its value or digest.
        exported = "key" in response and len(base64.b64decode(response["key"])) == 32
        assert not exported, "A general child bearer exported the permanent vault key"
    finally:
        authority.close()
        vault.clear_vault_cache()


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("mismatched_reply", [False, True])
def test_legacy_owner_only_confirms_an_independently_valid_key_fd(tmp_path, monkeypatch, mismatched_reply):
    from hermes_security.session_authority import ENV_NAME, start_session_authority
    from hermes_security.handoff import prepare_child_key

    home = tmp_path / "legacy-owner"
    monkeypatch.setenv("HERMES_HOME", str(home))
    vault.init_vault(home, "synthetic-legacy-password")
    unlocked = vault.unlock(home, "synthetic-legacy-password")
    authority = start_session_authority(home, "legacy-owner")
    grant = json.loads(authority.environment()[ENV_NAME])
    grant.pop("version")
    grant.pop("scope")
    raw = json.dumps(grant)
    key = os.urandom(32) if mismatched_reply else unlocked.master_key

    def legacy(request):
        if all(request.get(name) == grant[name] for name in ("token", "home", "session_id")):
            return {"home": str(home), "key": base64.b64encode(key).decode("ascii")}
        return {"error": "refused"}

    monkeypatch.setattr(authority, "respond", legacy)
    monkeypatch.setattr(authority, "environment", lambda **kwargs: {ENV_NAME: raw})
    monkeypatch.setenv(ENV_NAME, raw)
    env = dict(os.environ)
    env.pop("HERMES_VAULT_KEY_FD", None)
    code = """
from hermes_security.handoff import adopt_inherited_key
try:
    assert adopt_inherited_key()
except Exception:
    raise SystemExit(2)
print('trusted-fd-confirmed')
"""
    fd = None
    try:
        result = subprocess.run([sys.executable, "-c", code], env=env, cwd=Path(__file__).resolve().parents[2],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20, check=False)
        assert result.returncode == 2 and not result.stdout
        command = "from hermes_cli.management import main; raise SystemExit(main(['apply']))"
        result = subprocess.run([sys.executable, "-c", command], env=env, cwd=Path(__file__).resolve().parents[2],
                                input=json.dumps({"op": "config.get"}), capture_output=True, text=True, timeout=20, check=False)
        response = json.loads(result.stdout)
        assert result.returncode == 2 and response["error"]["code"] == "owner_restart_required"
        assert "Restart the owning session" in response["error"]["message"]
        fd, additions = prepare_child_key(home)
        result = subprocess.run([sys.executable, "-c", code], env={**env, **additions}, pass_fds=(fd,),
                                cwd=Path(__file__).resolve().parents[2], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=20, check=False)
        assert result.returncode == (2 if mismatched_reply else 0), "legacy owner disagreed with trusted FD custody"
        assert bool(result.stdout) is not mismatched_reply
    finally:
        if fd is not None:
            os.close(fd)
        authority.close()
        vault.clear_vault_cache()


@pytest.mark.parametrize("arguments", [
    ["--model", "synthetic-model", "chat", "--query=synthetic query"],
    ["--provider", "custom", "--oneshot=synthetic query"],
    ["chat", "--help"],
    ["manage", "run", "claude", "--", "-p", "synthetic query"],
])
def test_native_proxy_keeps_public_headless_option_ordering(arguments):
    from hermes_security.authority_native import checked_arguments

    assert checked_arguments(arguments) == arguments


def test_native_profile_prefix_is_bound_to_the_owner_instead_of_launch_env(tmp_path, monkeypatch):
    from hermes_security.authority_native import profile_arguments

    home = tmp_path / "owned" / "profiles" / "work"
    home.mkdir(parents=True)
    (home / "config.yaml").write_bytes(b"synthetic profile identity")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "unrelated-launch-home"))
    arguments = ["config", "get", "agent.max_turns"]
    assert profile_arguments(home, ["-p", "Work", *arguments]) == arguments
    with pytest.raises(ValueError, match="cannot select another profile"):
        profile_arguments(home, ["-p", "default", *arguments])


@pytest.mark.parametrize("arguments", [
    ["manage", "run", "hermes", "--", "pm", "run", "python", "-c", "synthetic"],
    ["manage", "run", "hermes", "--", "manage", "run", "hermes", "--", "--run-module", "os"],
    ["manage", "run", "hermes"],
])
def test_nested_native_runner_cannot_bypass_command_or_interactive_refusal(arguments):
    from hermes_security.authority_native import checked_arguments

    with pytest.raises(ValueError):
        checked_arguments(arguments)
