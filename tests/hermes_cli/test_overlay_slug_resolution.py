"""Test that overlay providers with mismatched models.dev keys resolve correctly.

HERMES_OVERLAYS keys may be models.dev IDs (e.g. "github-copilot") while
_PROVIDER_MODELS and config.yaml use Hermes IDs ("copilot").  The slug
resolution in list_authenticated_providers() Section 2 must bridge this gap.

Covers: #5223, #6492
"""

import os
from unittest.mock import patch


from hermes_cli.model_switch import list_authenticated_providers


# -- Copilot slug resolution (env var path) ----------------------------------





# -- kimi-for-coding alias in auth.py ----------------------------------------



# -- Generic slug mismatch providers -----------------------------------------






def test_mapped_provider_credential_pool_visibility(monkeypatch):
    """Mapped providers should appear when credentials live only in auth-store credential_pool."""
    monkeypatch.setattr("agent.models_dev.fetch_models_dev", lambda: {"z-ai": {"env": ["ZAI_API_KEY"]}})
    monkeypatch.setattr("agent.models_dev.PROVIDER_TO_MODELS_DEV", {"zai": "z-ai"})
    monkeypatch.setattr(
        "hermes_cli.auth._load_auth_store",
        lambda: {"providers": {}, "credential_pool": {"zai": {"token": "fake"}}},
    )
    monkeypatch.delenv("ZAI_API_KEY", raising=False)

    providers = list_authenticated_providers(current_provider="zai")

    zai = next((p for p in providers if p["slug"] == "zai"), None)
    assert zai is not None, "zai should appear when auth-store credential_pool has creds"
    assert zai["is_current"] is True
    assert zai["total_models"] > 0
