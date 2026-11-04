"""Bounded real-process management cohort; original assertions and fixtures retained."""

from __future__ import annotations
import os
import secrets
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import pytest
from tests.hermes_cli.test_management_public_cli import (ROOT)

pytest_plugins = ["tests.hermes_cli.test_management_public_cli"]
pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]


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
    from hermes_cli.config import save_config
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_security import vault
    from tests.e2e.vault_home import prepare_fixture_runtime

    other_password = secrets.token_urlsafe(32)
    vault.init_vault(other, other_password)
    vault.unlock(other, other_password)
    token = set_hermes_home_override(other)
    try:
        save_config({"agent": {"max_turns": 11}, "updates": {"check": False}}, strip_defaults=False)
        prepare_fixture_runtime(other, ROOT)
    finally:
        reset_hermes_home_override(token)
    result, output = world.call({"op": "config.get", "path": []}, env=dict(world.env, HERMES_HOME=str(other)))
    assert result.returncode != 0 and output["ok"] is False
    damaged = original[:-1] + bytes([original[-1] ^ 1])
    (world.home / "config.yaml").write_bytes(damaged)
    result, output = world.call({"op": "config.set", "path": ["agent", "max_turns"],
                               "value": 100, "revision": revision})
    assert result.returncode != 0 and output["ok"] is False
    assert (world.home / "config.yaml").read_bytes() == damaged


def test_operation_grant_preserves_large_public_native_output_and_rejects_execution_escapes(world):
    from hermes_cli._launchers import runtime_command
    from hermes_security.session_authority import ENV_NAME, operation_request

    value = "synthetic-native-output-" + "x" * 24000
    result, output = world.call({"op": "config.set", "path": ["skills", "config", "large"],
                                "value": value, "revision": world.get()["revision"]})
    assert result.returncode == 0, output
    result = subprocess.run(runtime_command(ROOT, ["config", "get", "skills.config.large"]),
                            cwd=ROOT, env=world.env, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, world.diagnostic(result.stderr)
    assert result.stdout.strip() == value
    for arguments in (["pm", "run", "python", "-c", "print('escape')"], ["--run-module", "os"], [],
                      ["chat", "-q", "synthetic", "--in", "/tmp"],
                      ["cron", "create", "--script", "/tmp/arbitrary.py"]):
        response = operation_request(world.home, world.env[ENV_NAME],
                                     {"op": "native.start", "arguments": arguments})
        assert response.get("ok") is False
    result = subprocess.run(runtime_command(ROOT, ["config", "get", "--not-a-real-flag"]),
                            cwd=ROOT, env=world.env, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode != 0


def test_trusted_fd_owner_can_import_the_cli_backend_after_handoff(world):
    from hermes_security.handoff import prepare_child_key

    fd, additions = prepare_child_key(world.home)
    script = """
import sys
from hermes_security.handoff import adopt_inherited_key
assert adopt_inherited_key()
sys.argv = ['embedded-native-owner']
import hermes_cli.main
print('native-backend-imported')
"""
    try:
        result = subprocess.run([sys.executable, "-c", script], env={**world.env, **additions}, cwd=ROOT,
                                pass_fds=(fd,), stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=60, check=False)
        assert result.returncode == 0, world.diagnostic(result.stderr)
        assert "native-backend-imported" in result.stdout
    finally:
        os.close(fd)
