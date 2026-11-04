"""Bounded real-process management cohort; original assertions and fixtures retained."""

from __future__ import annotations
import json
import sys
import pytest
from tests.hermes_cli.test_managed_agent_supervision import (_OPEN_DESCRIPTORS, _log, _public_owner)

pytest_plugins = ["tests.hermes_cli.test_managed_agent_supervision"]
pytestmark = pytest.mark.live_system_guard_bypass


@pytest.mark.platforms("linux", "macos")
def test_trusted_hermes_runner_unlocks_through_its_inherited_key_fd(request, world, tmp_path):
    log = tmp_path / "owner.log"
    owner = _public_owner(request, tmp_path, ["hermes", "config", "get", "agent.max_turns"], log)
    assert owner.wait(timeout=120) == 0, world.diagnostic(_log(log))
    assert "19" in log.read_text(encoding="utf-8").split()


@pytest.mark.platforms("linux", "macos")
def test_vendor_runner_inherits_no_key_or_supervision_descriptor(request, tmp_path):
    log = tmp_path / "owner.log"
    executable = tmp_path / "codex"
    executable.write_text(f"#!{sys.executable}\n" + _OPEN_DESCRIPTORS + """
print(json.dumps({"key_fd": "HERMES_VAULT_KEY_FD" in os.environ, "extra": open_descriptors()}))
sys.exit(5)
""", encoding="utf-8")
    executable.chmod(0o700)
    owner = _public_owner(request, tmp_path, ["codex"], log)
    assert owner.wait(timeout=120) == 5, _log(log)
    reported = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert reported == {"key_fd": False, "extra": []}
