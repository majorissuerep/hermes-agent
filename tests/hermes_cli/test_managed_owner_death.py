"""Bounded real-process management cohort; original assertions and fixtures retained."""

from __future__ import annotations
import pytest
from tests.hermes_cli.test_managed_agent_supervision import (_ENTRIES, _assert_tree_gone, _recorded_tree, _stop_owner, _tree_owner)

pytest_plugins = ["tests.hermes_cli.test_managed_agent_supervision"]
pytestmark = pytest.mark.live_system_guard_bypass


@_ENTRIES
@pytest.mark.platforms("linux")
def test_owner_death_removes_every_managed_descendant(request, tmp_path, identities, entry):
    record, log = tmp_path / "tree.json", tmp_path / "owner.log"
    owner = _tree_owner(request, entry, tmp_path, record, log)
    try:
        _recorded_tree(owner, record, log, identities)
        owner.kill()
        owner.wait(timeout=30)
        _assert_tree_gone(identities, log)
    finally:
        _stop_owner(owner)
