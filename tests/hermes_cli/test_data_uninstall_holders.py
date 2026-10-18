"""Data deletion must establish quiescence, never infer it from a signal count."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermes_cli import uninstall
from tests.hermes_cli.test_data_uninstall import layout




def test_live_chat_lease_blocks_data_deletion(layout):
    from hermes_cli.active_sessions import release_active_session, try_acquire_active_session

    home, _, data = layout
    lease, refusal = try_acquire_active_session(
        session_id="fixture-session", surface="cli", config={}, registry_home=home,
        track_liveness=True,
    )
    assert lease is not None and refusal is None
    try:
        with pytest.raises(SystemExit):
            uninstall.run_data_uninstall(SimpleNamespace(yes=True))
        assert all(path.exists() for path in data)
    finally:
        release_active_session(lease)






@pytest.mark.parametrize("terminal", [False, True])
def test_detached_cron_attempt_must_finish_before_data_removal(layout, monkeypatch, terminal):
    from cron import executions

    _, _, data = layout
    monkeypatch.setattr(executions, "_emit_execution_state", lambda *args, **kwargs: None)
    record = executions.create_execution("temporary-job", source="manual")
    if terminal:
        executions.finish_execution(record["id"], success=True)
        uninstall.run_data_uninstall(SimpleNamespace(yes=True))
        assert all(not path.exists() for path in data)
    else:
        with pytest.raises(SystemExit):
            uninstall.run_data_uninstall(SimpleNamespace(yes=True))
        assert all(path.exists() for path in data)
