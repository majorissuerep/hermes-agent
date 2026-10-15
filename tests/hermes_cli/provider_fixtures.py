"""Explicit registry opt-ins for extension contracts, never bundled inference defaults."""


def install_optional_aggregator(monkeypatch):
    from hermes_cli.auth import PROVIDER_REGISTRY, ProviderConfig

    monkeypatch.setitem(PROVIDER_REGISTRY, "openrouter", ProviderConfig(
        id="openrouter", name="Optional aggregator fixture", auth_type="api_key",
        inference_base_url="https://optional-aggregator.invalid/v1",
        api_key_env_vars=("TEST_OPTIONAL_AGGREGATOR_KEY",)))
