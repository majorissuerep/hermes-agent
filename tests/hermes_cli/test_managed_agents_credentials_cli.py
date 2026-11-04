"""Bounded real-process management cohort; original assertions and fixtures retained."""

from __future__ import annotations
import os
import secrets
import subprocess
import sys
import pytest
from tests.hermes_cli.test_management_public_cli import ROOT

pytest_plugins = ["tests.hermes_cli.test_managed_agents_public_cli"]
pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]


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
