"""Bounded real-process management cohort; original assertions and fixtures retained."""

from __future__ import annotations
import json
import os
import subprocess
import pytest
from tests.hermes_cli.test_management_public_cli import ROOT
from tests.hermes_cli.test_managed_agents_public_cli import (_fake_runner)

pytest_plugins = ["tests.hermes_cli.test_managed_agents_public_cli"]
pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]


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
