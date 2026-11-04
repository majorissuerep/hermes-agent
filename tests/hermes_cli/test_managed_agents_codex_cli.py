"""Native executor callbacks with the original bounded, encrypted-owner contract."""
import pytest
from tests.hermes_cli.test_managed_agents_public_cli import _assert_managed_coding_executor

pytest_plugins = ["tests.hermes_cli.test_managed_agents_public_cli"]
pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]


@pytest.mark.parametrize(("runner", "configured"), [('codex', False), ('codex', True)], ids=['False-codex', 'True-codex'])
def test_managed_coding_executor_calls_owning_encrypted_cli_without_another_unlock(
        world, tmp_path, ram_runtime, runner, configured, monkeypatch):
    _assert_managed_coding_executor(world, tmp_path, ram_runtime, runner, configured, monkeypatch)
