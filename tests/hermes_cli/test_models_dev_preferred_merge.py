"""Tests for the models.dev-preferred merge behavior in provider_model_ids
and list_authenticated_providers.

These guard the contract:

  * For providers in ``_MODELS_DEV_PREFERRED`` (opencode-go, opencode-zen,
    xiaomi, deepseek, smaller inference providers), both the CLI model
    picker path (``provider_model_ids``) and the gateway ``/model`` picker
    path (``list_authenticated_providers``) merge fresh models.dev entries
    on top of the curated static list.
  * OpenRouter and Nous Portal are NEVER merged — they keep their curated
    (OpenRouter) or live-Portal (Nous) semantics.
  * If models.dev is unreachable (offline / CI), the curated list is the
    fallback — no crash, no empty list.

Merging is what lets new models (e.g. ``mimo-v2.5-pro`` on opencode-go)
appear in ``/model`` without a Hermes release.
"""

from unittest.mock import patch


from hermes_cli.models import (
    _PROVIDER_MODELS,
    _merge_with_models_dev,
    provider_model_ids,
)


class TestMergeHelper:

    def test_merge_empty_mdev_returns_curated(self):
        """When models.dev returns nothing, curated list is preserved verbatim."""
        with patch("agent.models_dev.list_agentic_models", return_value=[]):
            out = _merge_with_models_dev("zai", ["mimo-v2-pro", "kimi-k2.6"])
        assert out == ["mimo-v2-pro", "kimi-k2.6"]

    def test_merge_case_insensitive_dedup(self):
        """Dedup is case-insensitive but preserves the first occurrence's casing."""
        mdev = ["MiniMax-M2.7"]
        curated = ["minimax-m2.7", "minimax-m2.5"]
        with patch("agent.models_dev.list_agentic_models", return_value=mdev):
            out = _merge_with_models_dev("zai", curated)
        # models.dev casing wins since it came first
        assert out == ["MiniMax-M2.7", "minimax-m2.5"]




def test_setup_menu_merge_leads_with_curated_in_full():
    from hermes_cli.model_setup_flows_common import _models_dev_merged
    with patch("agent.models_dev.list_agentic_models", return_value=["a/x", "b/Y", "c/z"]):
        assert _models_dev_merged("ai-gateway", ["B/y", "d/w"]) == ["b/Y", "d/w", "a/x", "c/z"]
        assert _models_dev_merged("zai", ["B/y", "d/w"]) == ["b/Y", "d/w", "a/x", "c/z"]
