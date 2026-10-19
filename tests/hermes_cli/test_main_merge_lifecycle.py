"""Behavior parity at lifecycle helpers extracted before the main merge."""

import pytest


@pytest.mark.parametrize('provider,host,expected', [
    ('nous', 'https://welcome-api.nousresearch.com/v1', True),
    ('nous', 'https://inference-api.nousresearch.com/v1', False),
    ('custom', 'https://welcome-api.nousresearch.com/v1', False),
])
def test_readiness_reports_selected_free_route(monkeypatch, provider, host, expected):
    from hermes_cli.runtime_readiness import check_runtime_readiness
    monkeypatch.setattr('hermes_cli.main._has_any_provider_configured', lambda **kw: True)
    monkeypatch.setattr('hermes_cli.runtime_provider.resolve_runtime_provider', lambda **kw: {
        'provider': provider, 'base_url': host, 'api_key': 'no-key-required', 'model': 'nous/welcome'})
    # Native setup readiness reports the selected route, not ambient profile state.
    assert check_runtime_readiness()['free_tier_route'] is expected


def test_canonical_readiness_blames_the_pin_not_the_chain_tail(monkeypatch):
    """#124939 on the authority path: a chain that resolves only at its tail must not send
    onboarding after a provider the user never pinned."""
    from hermes_cli.runtime_readiness import check_runtime_readiness
    monkeypatch.setattr('hermes_cli.main._has_any_provider_configured', lambda **kw: True)
    monkeypatch.setattr('hermes_cli.config.load_config',
                        lambda: {'model': {'provider': 'zai', 'default': 'glm-5.3'}})
    monkeypatch.setattr('hermes_cli.runtime_provider.resolve_runtime_provider', lambda **kw: {
        'provider': 'openrouter', 'api_key': '', 'source': 'pool'})
    result = check_runtime_readiness()
    assert result == {'ok': False, 'provider': 'zai', 'model': 'glm-5.3', 'source': 'pool',
                      'error': 'No usable credentials found for zai.'}
