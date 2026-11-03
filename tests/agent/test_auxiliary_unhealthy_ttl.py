"""Provider backoff remains active before its deadline and expires exactly at it."""
from types import SimpleNamespace


def test_ttl_expiry_evicts(monkeypatch):
    from agent import auxiliary_client as auxiliary

    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(auxiliary.time, "time", lambda: clock.now)
    auxiliary._reset_aux_unhealthy_cache()
    try:
        auxiliary._mark_provider_unhealthy("openrouter", ttl=60)
        assert auxiliary._is_provider_unhealthy("openrouter") is True
        assert list(auxiliary._aux_unhealthy_until.values()) == [1060.0]

        clock.now = 1059.999
        assert auxiliary._is_provider_unhealthy("openrouter") is True

        clock.now = 1060.0
        assert auxiliary._is_provider_unhealthy("openrouter") is False
        assert not auxiliary._aux_unhealthy_until
        assert not auxiliary._aux_unhealthy_reason
        assert not auxiliary._aux_unhealthy_logged_at
    finally:
        auxiliary._reset_aux_unhealthy_cache()
