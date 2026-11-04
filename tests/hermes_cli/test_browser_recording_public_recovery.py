"""A completed recorder's RAM capture is recoverable through the public encrypted CLI.

The producer uses the real lease/codec in its own process. This is recovery CLI
evidence, not evidence of a real browser recording or playable video container.
"""
from __future__ import annotations

import json
from pathlib import Path
import secrets
import subprocess
import sys

import pytest

pytest_plugins = ["tests.hermes_cli.test_managed_agents_public_cli"]
pytestmark = pytest.mark.platforms("linux")
ROOT = Path(__file__).resolve().parents[2]
_PRODUCER = """
import json, sys
from hermes_security.handoff import adopt_inherited_key
assert adopt_inherited_key()
from hermes_constants import get_hermes_home
from hermes_security import artifacts
from tools.browser_recording import _new_name
from tools import browser_recording_pending as pending
home = get_hermes_home().resolve()
custody = artifacts._bound_custody(home)
lease = pending.allocate(home, custody, _new_name())
data = sys.stdin.buffer.read(65537)
assert 0 < len(data) <= 65536
pending.store_capture(lease, custody, data)
pending.completed(lease, custody)
print(json.dumps({'name': lease.name, 'workspace': str(lease.workspace),
                  'locator': str(lease.locator.relative_to(home))}))
"""


# The real launcher embeds its frozen updater-recovery closure. This native
# integration runs only management/status/repair against the synthetic home.
@pytest.mark.live_system_guard_bypass
def test_public_repair_recovers_an_ended_recorder_without_reunlocking_parent(world, ram_runtime):
    from hermes_cli._launchers import runtime_command
    from hermes_security import artifacts
    from hermes_security.session_authority import ENV_NAME
    from tests.e2e.vault_home import run_unlocked, spawn_unlocked

    revision = world.get()["revision"]
    configured, _ = world.call({"op": "config.set", "path": ["security", "protected_work_dir"],
                                "value": str(ram_runtime), "revision": revision})
    assert configured.returncode == 0
    # This is the human's finite native request, not the external executor's operation-only grant.
    env = {key: value for key, value in world.env.items() if key not in {ENV_NAME, "HERMES_SESSION_ID"}}
    payload = secrets.token_bytes(4096)  # private canary never appears in source, argv, env, or printed evidence
    with spawn_unlocked([sys.executable, "-c", _PRODUCER], env=env, cwd=ROOT,
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as producer:
        try:
            stdout, stderr = producer.communicate(payload, timeout=60)
        except subprocess.TimeoutExpired:
            producer.kill()
            producer.communicate()
            raise
    assert producer.returncode == 0, world.diagnostic(stderr)
    result = json.loads(stdout)
    workspace = Path(result["workspace"])
    assert workspace.parent == ram_runtime and workspace.is_dir()
    assert (world.home / result["locator"]).read_bytes().startswith(b"HRMVAULT\0")

    status = run_unlocked(runtime_command(ROOT, ["secure-vault", "status"]), env=env,
                          cwd=ROOT, text=True, capture_output=True, timeout=90)
    assert status.returncode == 0, world.diagnostic((status.stdout, status.stderr))
    assert "Pending recordings: 1" in status.stdout
    repaired = run_unlocked(runtime_command(ROOT, ["secure-vault", "repair"]), env=env,
                            cwd=ROOT, text=True, capture_output=True, timeout=90)
    assert repaired.returncode == 0, world.diagnostic((repaired.stdout, repaired.stderr))
    destination = world.home / "artifacts" / "media" / "videos" / result["name"]
    exact = artifacts.read_bytes(destination, home=world.home) == payload
    assert exact
    assert not workspace.exists() and not (world.home / result["locator"]).exists()
    private_bytes_on_disk = any(payload in path.read_bytes() for path in world.home.rglob("*")
                                if path.is_file() and not path.is_symlink())
    assert not private_bytes_on_disk
    assert world.get(["agent", "max_turns"])["value"] == 19
