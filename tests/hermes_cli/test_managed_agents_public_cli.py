"""Managed coding executors use the owning encrypted native CLI in fresh processes."""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys

import pytest

from tests.hermes_cli.test_management_public_cli import ROOT

pytest_plugins = ["tests.hermes_cli.test_management_public_cli"]
pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]


@pytest.mark.parametrize("runner", ["codex", "claude"])
@pytest.mark.parametrize("configured", [False, True])
def test_managed_coding_executor_calls_owning_encrypted_cli_without_another_unlock(world, tmp_path, runner, configured):
    from hermes_cli._launchers import runtime_command
    from hermes_security import io

    credentials = "OPENAI_API_KEY=synthetic-codex\nANTHROPIC_API_KEY=synthetic-claude\n" if configured else ""
    io.write_text(world.home / ".env", credentials +
                  "OPENAI_API_BASE=https://legacy.profile.invalid/v1\nOPENAI_BASE_URL=https://codex.profile.invalid/v1\n"
                  "ANTHROPIC_BASE_URL=https://claude.profile.invalid\n", purpose="env")
    directory = tmp_path / "executors"
    directory.mkdir()
    wrong_hermes = directory / "hermes"
    wrong_hermes.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
    wrong_hermes.chmod(0o700)
    executable = directory / runner
    executable.write_text(f"#!{sys.executable}\n" + """
import json, os, subprocess, sys
name = os.path.basename(sys.argv[0])
configured = json.load(sys.stdin)['expect_profile_api']
assert ('--ephemeral' in sys.argv) if name == 'codex' else ('--no-session-persistence' in sys.argv and '--print' in sys.argv)
expected = 'CODEX_API_KEY' if name == 'codex' else 'ANTHROPIC_API_KEY'
foreign = 'ANTHROPIC_API_KEY' if name == 'codex' else 'CODEX_API_KEY'
assert foreign not in os.environ and 'GH_TOKEN' not in os.environ
assert 'OPENAI_API_KEY' not in os.environ and 'CLAUDE_CODE_OAUTH_TOKEN' not in os.environ
endpoint = 'OPENAI_BASE_URL' if name == 'codex' else 'ANTHROPIC_BASE_URL'
if configured:
    assert os.environ[expected] == 'synthetic-' + name
    assert os.environ[endpoint] == ('https://codex.profile.invalid/v1' if name == 'codex' else 'https://claude.profile.invalid')
else:
    assert expected not in os.environ and endpoint not in os.environ
assert ('ANTHROPIC_BASE_URL' if name == 'codex' else 'OPENAI_BASE_URL') not in os.environ
assert 'HERMES_VAULT_KEY_FD' not in os.environ and 'HERMES_MASTER_PASSWORD' not in os.environ
request = {'op': 'config.get', 'path': ['agent', 'max_turns']}
read = subprocess.run(['/bin/bash', '-lc', 'hermes manage apply'], input=json.dumps(request), text=True, capture_output=True, timeout=60)
assert read.returncode == 0
result = json.loads(read.stdout)['result']
assert result['value'] == 19
request = {'op': 'config.set', 'path': ['agent', 'max_turns'], 'value': 47, 'revision': result['revision']}
write = subprocess.run(['/bin/bash', '-lc', 'hermes manage apply'], input=json.dumps(request), text=True, capture_output=True, timeout=60)
assert write.returncode == 0
print('encrypted-child-management-ok')
""")
    executable.chmod(0o700)
    env = dict(world.env, PATH=str(directory) + os.pathsep + world.env["PATH"], GH_TOKEN="synthetic-foreign-github",
               CODEX_API_KEY="synthetic-foreign-codex", CLAUDE_CODE_OAUTH_TOKEN="synthetic-foreign-claude",
               OPENAI_API_KEY="synthetic-foreign-openai", ANTHROPIC_API_KEY="synthetic-foreign-anthropic",
               OPENAI_BASE_URL="https://foreign.invalid/v1", ANTHROPIC_BASE_URL="https://foreign.invalid")
    result = subprocess.run(runtime_command(ROOT, ["manage", "run", runner]),
                            env=env, cwd=ROOT, input=json.dumps({"expect_profile_api": configured}), capture_output=True, text=True,
                            timeout=120, check=False)
    assert result.returncode == 0, world.diagnostic((result.stdout, result.stderr), env=env)
    assert result.stdout.strip() == "encrypted-child-management-ok"
    assert world.get(["agent", "max_turns"])["value"] == 47
    assert (world.home / "config.yaml").read_bytes().startswith(b"HRMVAULT")



def test_managed_native_cli_keeps_parent_unlocked_and_standalone_refuses(world):
    from hermes_cli._launchers import runtime_command

    command = runtime_command(ROOT, ["manage", "run", "hermes", "config", "get", "agent.max_turns"])
    result = subprocess.run(command, env=world.env, cwd=ROOT, stdin=subprocess.DEVNULL,
                            text=True, capture_output=True, timeout=90, check=False)
    assert result.returncode == 0 and "19" in result.stdout, world.diagnostic((result.stdout, result.stderr))
    assert world.get(["agent", "max_turns"])["value"] == 19
    independent = {k: v for k, v in world.env.items() if k != "HERMES_SESSION_VAULT"}
    refused = subprocess.run(command, env=independent, cwd=ROOT, stdin=subprocess.DEVNULL,
                             text=True, capture_output=True, timeout=90, check=False)
    assert refused.returncode != 0
    assert json.loads(refused.stdout)["ok"] is False



def test_managed_runner_removes_the_launch_profiles_custom_secret_residue(world, tmp_path):
    from hermes_cli._launchers import runtime_command
    from hermes_cli.config import save_config
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_security import io, vault
    from tests.e2e.vault_home import prepare_fixture_runtime, spawn_unlocked

    io.write_text(world.home / ".env", "LAUNCH_PROFILE_TOKEN=synthetic-foreign-secret\n", purpose="env")
    selected = world.home / "profiles" / "work"
    password = secrets.token_urlsafe(32)
    vault.init_vault(selected, password)
    vault.unlock(selected, password)
    token = set_hermes_home_override(selected)
    try:
        save_config({"agent": {"max_turns": 61}, "updates": {"check": False}}, strip_defaults=False)
        io.write_text(selected / ".env", "OPENAI_API_KEY=synthetic-selected-key\n", purpose="env")
        prepare_fixture_runtime(selected, ROOT)
    finally:
        reset_hermes_home_override(token)
    executable = tmp_path / "codex"
    executable.write_text(f"#!{sys.executable}\n" + """
import os
assert 'LAUNCH_PROFILE_TOKEN' not in os.environ
assert os.environ['CODEX_API_KEY'] == 'synthetic-selected-key'
assert 'HERMES_VAULT_KEY_FD' not in os.environ
print('profile-scoped-runner-ok')
""", encoding="utf-8")
    executable.chmod(0o700)
    env = dict(world.env, PATH=str(tmp_path) + os.pathsep + world.env["PATH"],
               LAUNCH_PROFILE_TOKEN="synthetic-foreign-secret")
    env.pop("HERMES_SESSION_VAULT", None)
    # The fixture's trusted maintenance parent names both keys explicitly; the
    # external executor must still receive only the selected profile's grant.
    with spawn_unlocked(runtime_command(ROOT, ["manage", "--profile", "work", "run", "codex"]),
                        env=env, additional_homes=(selected,), cwd=ROOT, stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
        stdout, stderr = process.communicate(timeout=120)
        assert process.returncode == 0, world.diagnostic((stdout, stderr), env=env)
        assert stdout.strip() == "profile-scoped-runner-ok"
