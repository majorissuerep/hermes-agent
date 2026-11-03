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


@pytest.fixture
def ram_runtime(monkeypatch):
    """A writable mount of this host proven never swapped; vendor scratch refuses anything else.

    The owning process's XDG_RUNTIME_DIR names it too: a forwarded run executes there.
    """
    import tempfile
    from pathlib import Path

    from hermes_security.protected_workspace import protected_workspace_status

    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as handle:
            mounts = [line.split(" - ", 1) for line in handle if " - " in line]
    except OSError:
        mounts = []
    for head, tail in mounts:
        point = head.split()[4].replace("\\040", " ")
        if tail.split()[0] in {"ramfs", "tmpfs"} and os.access(point, os.W_OK | os.X_OK):
            base = Path(tempfile.mkdtemp(dir=point, prefix="hermes-test-runtime-"))
            if protected_workspace_status(base).proven:
                monkeypatch.setenv("XDG_RUNTIME_DIR", str(base))
                yield base
                from tests.waiting import wait_until
                wait_until(lambda: not any(base.iterdir()), timeout=15,
                           what="the stopped managed owner's RAM scratch cleanup")
                os.rmdir(base)
                return
            os.rmdir(base)
    pytest.skip("this host has no writable RAM-only mount that is provably never swapped")


@pytest.mark.parametrize("runner", ["codex", "claude"])
@pytest.mark.parametrize("configured", [False, True])
def test_managed_coding_executor_calls_owning_encrypted_cli_without_another_unlock(
        world, tmp_path, ram_runtime, runner, configured, monkeypatch):
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
    # The operation owner chooses executors; caller PATH is deliberately different.
    monkeypatch.setenv("PATH", str(directory) + os.pathsep + os.environ["PATH"])
    caller = tmp_path / "caller-executors"
    caller.mkdir()
    for name in ("codex", "claude"):
        decoy = caller / name
        decoy.write_text("#!/bin/sh\nexit 92\n", encoding="utf-8")
        decoy.chmod(0o700)
    env = dict(world.env, PATH=str(caller) + os.pathsep + world.env["PATH"], GH_TOKEN="synthetic-foreign-github",
               CODEX_API_KEY="synthetic-foreign-codex", CLAUDE_CODE_OAUTH_TOKEN="synthetic-foreign-claude",
               OPENAI_API_KEY="synthetic-foreign-openai", ANTHROPIC_API_KEY="synthetic-foreign-anthropic",
               OPENAI_BASE_URL="https://foreign.invalid/v1", ANTHROPIC_BASE_URL="https://foreign.invalid",
               XDG_RUNTIME_DIR=str(ram_runtime))
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



def test_managed_runner_removes_the_launch_profiles_custom_secret_residue(world, tmp_path, ram_runtime):
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
               LAUNCH_PROFILE_TOKEN="synthetic-foreign-secret", XDG_RUNTIME_DIR=str(ram_runtime))
    env.pop("HERMES_SESSION_VAULT", None)
    # The fixture's trusted maintenance parent names both keys explicitly; the
    # external executor must still receive only the selected profile's grant.
    with spawn_unlocked(runtime_command(ROOT, ["manage", "--profile", "work", "run", "codex"]),
                        env=env, additional_homes=(selected,), cwd=ROOT, stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
        stdout, stderr = process.communicate(timeout=120)
        assert process.returncode == 0, world.diagnostic((stdout, stderr), env=env)
        assert stdout.strip() == "profile-scoped-runner-ok"


# Each row is a supported spelling (per the installed vendor help/parser) that
# relaxes the executor's approvals, sandbox, or runner-pinned persistence.
_RELAXING_ARGUMENTS = {
    "codex": (
        ["--dangerously-bypass-approvals-and-sandbox"], ["--yolo"], ["--dangerously-bypass-hook-trust"],
        ["--approve-for-me"], ["--add-dir", "/"], ["--ignore-rules"],
        ["--sandbox", "danger-full-access"], ["--sandbox=danger-full-access"], ["-s", "danger-full-access"],
        ["-sdanger-full-access"], ["-s=danger-full-access"],
        ["-c", 'sandbox_mode="danger-full-access"'], ["-c=sandbox_mode=danger-full-access"],
        ["--config", "approval_policy=never"], ["--config=ask_for_approval=never"],
        ["-c", ' "sandbox_workspace_write" . network_access = true'], ['-csandbox_workspace_write.writable_roots=["/"]'],
        ["-c", 'sandbox_permissions=["disk-full-read-access"]'], ["-c", "permissions.default.filesystem=write"],
        ["-c", "features.shell_snapshot=true"], ["-c", 'features."shell_snapshot_v2"=true'],
        ["-c", "features={shell_snapshot=true}"], ["--config", "history.persistence=save-all"],
        ["-c", 'history={persistence="save-all"}'], ["-c", "memories.generate_memories=true"],
        ["-c", "shell_environment_policy.ignore_default_excludes=true"],
        ["-c", "profiles.open.sandbox_mode=danger-full-access"], ["-c", 'projects."/".trust_level=trusted'],
        ["-c", "mcp_servers.escape.command=sh"],
    ),
    "claude": (
        ["--dangerously-skip-permissions"], ["--allow-dangerously-skip-permissions"],
        ["--permission-mode", "bypassPermissions"], ["--permission-mode=bypassPermissions"],
        ["--permission-mode", "acceptEdits"], ["--permission-mode=auto"], ["--add-dir", "/"],
        ["-pc"], ["-pr"], ["--from-pr", "7"], ["--teleport"], ["--cloud", "task"], ["--fork-session"],
        ["--settings", '{"permissions": {"defaultMode": "bypassPermissions"}}'], ["--settings=bypass.json"],
    ),
}


def _fake_runner(tmp_path, monkeypatch, runner):
    directory = tmp_path / "executors"
    directory.mkdir()
    executable = directory / runner
    executable.write_text("#!/bin/sh\necho executor-started\n", encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(directory) + os.pathsep + os.environ.get("PATH", ""))
    return executable


@pytest.mark.parametrize(("runner", "arguments"), [
    (runner, arguments) for runner, rows in _RELAXING_ARGUMENTS.items() for arguments in rows
], ids=lambda value: " ".join(value) if isinstance(value, list) else value)
def test_managed_runner_refuses_permission_and_sandbox_relaxation(tmp_path, monkeypatch, runner, arguments):
    from hermes_cli.managed_agents import _command
    from hermes_cli.management_config import ManagementError

    _fake_runner(tmp_path, monkeypatch, runner)
    with pytest.raises(ManagementError) as refused:
        _command(runner, ["-m" if runner == "codex" else "--model", "synthetic-model", *arguments, "task"])
    assert refused.value.code == "invalid_request"


@pytest.mark.parametrize("runner", ["codex", "claude"])
def test_public_managed_run_refuses_a_bypass_before_starting_the_executor(world, tmp_path, monkeypatch, runner):
    from hermes_cli._launchers import runtime_command

    executable = _fake_runner(tmp_path, monkeypatch, runner)
    bypass = ("--dangerously-bypass-approvals-and-sandbox" if runner == "codex"
              else "--dangerously-skip-permissions")
    env = dict(world.env, PATH=str(executable.parent) + os.pathsep + world.env["PATH"])
    result = subprocess.run(runtime_command(ROOT, ["manage", "run", runner, "--", bypass, "task"]),
                            env=env, cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            timeout=120, check=False)
    diagnostic = world.diagnostic((result.returncode, result.stdout, result.stderr), env=env)
    assert "executor-started" not in result.stdout, diagnostic
    assert result.returncode != 0 and json.loads(result.stdout)["error"]["code"] == "invalid_request", diagnostic


@pytest.mark.parametrize(("runner", "arguments"), [
    ("codex", ["-m", "synthetic-model", "-c", 'model_reasoning_effort="high"', "--json", "-C", "/tmp",
               "-s", "workspace-write", "--sandbox=read-only", "--enable", "synthetic_feature",
               "--skip-git-repo-check", "task"]),
    ("claude", ["--model", "synthetic-model", "--effort", "high", "--output-format", "json",
                "--append-system-prompt", "be brief", "--permission-mode", "plan",
                "--permission-mode=dontAsk", "--permission-prompts", "none", "task"]),
])
def test_managed_runner_preserves_ordinary_options_and_its_defaults(tmp_path, monkeypatch, runner, arguments):
    from hermes_cli.managed_agents import _command

    executable = _fake_runner(tmp_path, monkeypatch, runner)
    command = _command(runner, list(arguments))
    assert command[0] == str(executable)
    if runner == "codex":
        assert command[1] == "exec" and command[2:2 + len(arguments)] == arguments
        assert {"--ephemeral", "history.persistence=none", "features.shell_snapshot=false",
                "memories.generate_memories=false"} <= set(command[2 + len(arguments):])
    else:
        assert command[-len(arguments):] == arguments
        assert {"--print", "--no-session-persistence"} <= set(command[1:-len(arguments)])
