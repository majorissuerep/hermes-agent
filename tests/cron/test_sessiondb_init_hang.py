"""Local store acquisition is bounded, profile-scoped, and never falls back."""
import threading
from unittest.mock import MagicMock

import pytest


def test_local_store_acquisition_preserves_profile_scope(tmp_path, monkeypatch):
    from cron import scheduler
    from hermes_constants import get_hermes_home, set_hermes_home_override, reset_hermes_home_override

    observed = []
    db = object()
    def acquire():
        observed.append(get_hermes_home())
        return db
    monkeypatch.setattr("hermes_state.registry.acquire", acquire)
    token = set_hermes_home_override(tmp_path)
    try:
        assert scheduler._open_cron_session_db({}) is db
    finally:
        reset_hermes_home_override(token)
    assert observed == [tmp_path]


def test_local_store_timeout_releases_late_acquisition(monkeypatch):
    from cron import scheduler

    release = threading.Event()
    closed = threading.Event()
    db = MagicMock()
    db.close.side_effect = closed.set
    def acquire():
        assert release.wait(5)
        return db
    monkeypatch.setattr("hermes_state.registry.acquire", acquire)
    monkeypatch.setattr(scheduler, "_get_session_db_timeout", lambda: 0.01)
    try:
        with pytest.raises(TimeoutError, match="acquisition timed out"):
            scheduler._open_cron_session_db({})
    finally:
        release.set()
    assert closed.wait(5)


def test_local_store_error_propagates_without_fallback(monkeypatch):
    from cron import scheduler

    def acquire():
        raise OSError("encrypted store unavailable")
    monkeypatch.setattr("hermes_state.registry.acquire", acquire)
    with pytest.raises(OSError, match="encrypted store unavailable"):
        scheduler._open_cron_session_db({})
