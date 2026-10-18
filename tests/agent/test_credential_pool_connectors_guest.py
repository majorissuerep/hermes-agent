"""An installed optional provider's guest token is unusable once that provider is removed."""

from hermes_cli import anon_auth
from tests.hermes_cli.anon_portal import claimed_trial, install_portal


def test_guest_pool_row_is_dropped_when_optional_provider_is_removed(monkeypatch, tmp_path):
    from agent.credential_pool import load_pool
    from hermes_cli.auth_nous import resolve_nous_runtime_credentials

    install_portal(monkeypatch, tmp_path)
    claimed_trial(monkeypatch)
    resolve_nous_runtime_credentials()  # the claimed guest now carries an inference token
    assert any(e.source == "device_code" for e in load_pool("nous").entries())

    monkeypatch.setattr(anon_auth, "welcome_provider_available", lambda: False)

    assert not any(e.source == "device_code" for e in load_pool("nous").entries())
    assert anon_auth.has_guest()
