"""Configured models extend built-in picker rows."""

from unittest.mock import patch

from hermes_cli.model_switch import list_authenticated_providers


def _provider_row(configured_models, *, max_models=None):
    with (
        patch(
            "agent.models_dev.fetch_models_dev",
            return_value={"zai": {"env": ["GLM_API_KEY"], "name": "Z.AI"}},
        ),
        patch(
            "agent.models_dev.PROVIDER_TO_MODELS_DEV",
            {"zai": "zai"},
        ),
        patch(
            "hermes_cli.models.cached_provider_model_ids",
            return_value=["live-a", "shared"],
        ),
        patch("hermes_cli.providers.HERMES_OVERLAYS", {}),
        patch.dict("os.environ", {"GLM_API_KEY": "test-key"}),
    ):
        rows = list_authenticated_providers(
            current_provider="zai",
            user_providers={"zai": {"models": configured_models}},
            max_models=max_models,
        )
    return next(row for row in rows if row["slug"] == "zai")


def test_configured_models_precede_and_deduplicate_discovered_models():
    row = _provider_row({"configured-x": {}, "shared": {}})

    assert row["models"] == ["configured-x", "shared", "live-a"]
    assert row["total_models"] == 3


