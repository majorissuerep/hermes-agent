"""Live routing persists the established prompt, never a fresh profile snapshot."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tui_runtime import server


@pytest.mark.parametrize("prompt", [None, "Frozen profile instructions\nModel: initial\n"])
def test_live_prompt_persistence_never_rebuilds_after_route_change(prompt):
    database = SimpleNamespace(update_system_prompt=Mock())
    builder = Mock(return_value="NEW_MODEL_AND_RELOADED_MEMORY")
    agent = SimpleNamespace(_cached_system_prompt=prompt, _session_db=database,
                            session_id="execution-owner", _build_system_prompt=builder)
    server._persist_live_session_system_prompt({"agent": agent, "session_key": "display-owner"})
    builder.assert_not_called()
    assert agent._cached_system_prompt == prompt
    if prompt is None:
        database.update_system_prompt.assert_not_called()
    else:
        database.update_system_prompt.assert_called_once_with("execution-owner", prompt)
