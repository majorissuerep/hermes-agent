"""Tests for transport auto-detection in agent.auxiliary_client.

Auxiliary clients must pick the correct wire protocol (OpenAI
chat.completions vs native Anthropic Messages) based on the endpoint,
regardless of which resolve_provider_client branch built them.

Regression target (April 2026): Kimi Coding Plan's ``api.kimi.com/coding``
endpoint only speaks Anthropic Messages — sending ``kimi-for-coding`` over
chat.completions returns 404 "resource_not_found_error".  The named
``kimi-coding`` provider branch in resolve_provider_client used to build a
plain OpenAI client, so title generation / vision / compression /
web_extract all failed on Kimi Coding Plan users.
"""

from __future__ import annotations


import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "OPENAI_API_KEY", "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN",
        "KIMI_API_KEY", "KIMI_CODING_API_KEY", "KIMI_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# URL detection helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected,label", [
    ("https://api.kimi.com/coding/v1", True, "Kimi Coding Plan /v1"),
    ("https://api.kimi.com/coding", True, "Kimi Coding Plan no /v1"),
    ("https://api.moonshot.ai/v1", False, "Moonshot legacy"),
    ("https://api.minimax.io/anthropic", True, "MiniMax /anthropic"),
    ("https://litellm.example.com/v1/anthropic", True, "/anthropic suffix"),
    ("https://litellm.example.com/anthropic/v1", True, "/anthropic/v1 base"),
    ("https://litellm.example.com/anthropic/v1/models", False, "/anthropic/v1 subpath"),
    ("https://api.anthropic.com", True, "native Anthropic"),
    ("https://api.anthropic.com/v1", True, "native Anthropic /v1"),
    ("https://openrouter.ai/api/v1", False, "OpenRouter"),
    ("https://api.openai.com/v1", False, "OpenAI"),
    ("https://inference-api.nousresearch.com/v1", False, "Nous"),
    ("", False, "empty"),
    (None, False, "None"),
])
def test_endpoint_speaks_anthropic_messages(url, expected, label):
    from agent.auxiliary_client import _endpoint_speaks_anthropic_messages
    assert _endpoint_speaks_anthropic_messages(url) is expected, (
        f"{label}: {url!r} should be {expected}"
    )


# ---------------------------------------------------------------------------
# _maybe_wrap_anthropic decision table
# ---------------------------------------------------------------------------

















# ---------------------------------------------------------------------------
# Integration: resolve_provider_client for named kimi-coding provider
# ---------------------------------------------------------------------------
