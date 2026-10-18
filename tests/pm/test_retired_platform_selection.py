"""A terminal-only update keeps chosen local/provider features, not retired platforms."""
from __future__ import annotations

import json

import pytest

import pm
from pm.environments import runtime_facts_path
from tests.pm._fixtures import isolated_python as isolated_python  # noqa: PLC0414 -- pytest fixture registration
from tests.pm.test_source_update_launch import source_launch as source_launch  # noqa: PLC0414 -- load-bearing pytest fixture alias avoids F811 on its parameter

pytestmark = [pytest.mark.real_machine_home, pytest.mark.platforms("posix")]


def test_real_sync_retires_recorded_platform_extras_without_broadening(source_launch):
    root, _, _ = source_launch
    manifest = root / "pyproject.toml"
    original = manifest.read_text()
    manifest.write_text(original.replace(
        "launch-extra = []", "launch-extra = []\nanthropic = []\ncomputer-use = []\nmessaging = []\nslack = []\ntelegram = []"))
    pm.lock_project(root, offline=True, explicit=True)
    pm.sync_venv(["launch-extra", "anthropic", "computer-use", "messaging", "slack", "telegram"],
                 explicit=True, project_root=root)
    before = json.loads(runtime_facts_path(root).read_text())["packages"]["venv"]
    assert set(before["extras"]) == {"launch-extra", "anthropic", "computer-use", "messaging", "slack", "telegram"}

    manifest.write_text(manifest.read_text().replace("messaging = []\nslack = []\ntelegram = []", ""))
    pm.lock_project(root, offline=True, explicit=True)
    pm.sync_venv(explicit=True, project_root=root)
    after = json.loads(runtime_facts_path(root).read_text())["packages"]["venv"]
    assert after["extras"] == ["anthropic", "computer-use", "launch-extra"]
    assert after["environment"] != before["environment"]
    assert pm.venv_is_current(project_root=root)
