"""Parity tests: pin the exact current transport behavior per provider.

These tests document the flag-based contract between run_agent.py and
ChatCompletionsTransport.build_kwargs(). When the next PR wires profiles
to replace flags, every assertion here must still pass — any failure is
a behavioral regression.
"""

import pytest
from agent.transports.chat_completions import ChatCompletionsTransport
from providers import get_provider_profile


@pytest.fixture
def transport():
    return ChatCompletionsTransport()


def _simple_messages():
    return [{"role": "user", "content": "hello"}]


def _max_tokens_fn(n):
    return {"max_completion_tokens": n}


class TestCustomTokenLimit:
    """An explicit token limit overrides the profile default."""


    def test_user_max_tokens_overrides(self, transport):
        from providers import get_provider_profile

        profile = get_provider_profile("custom")
        kw = transport.build_kwargs(
            model="nvidia/llama-3.1-nemotron-70b-instruct",
            messages=_simple_messages(),
            tools=None,
            max_tokens=4096,
            max_tokens_param_fn=_max_tokens_fn,
            provider_profile=profile,
        )
        assert kw["max_completion_tokens"] == 4096  # user overrides default




















class TestCustomOllamaParity:
    """Custom/Ollama: num_ctx, thinking controls — now tested via profile."""

    def test_ollama_num_ctx(self, transport):
        kw = transport.build_kwargs(
            model="llama3.1",
            messages=_simple_messages(),
            tools=None,
            provider_profile=get_provider_profile("custom"),
            ollama_num_ctx=131072,
        )
        assert kw["extra_body"]["options"]["num_ctx"] == 131072

    def test_think_false_when_disabled(self, transport):
        kw = transport.build_kwargs(
            model="qwen3:72b",
            messages=_simple_messages(),
            tools=None,
            provider_profile=get_provider_profile("custom"),
            reasoning_config={"enabled": False, "effort": "none"},
            base_url="http://127.0.0.1:11434/v1",
        )
        assert kw["extra_body"]["think"] is False

    def test_think_omitted_for_mistral_custom(self, transport):
        kw = transport.build_kwargs(
            model="mistral-small-latest",
            messages=_simple_messages(),
            tools=None,
            provider_profile=get_provider_profile("custom"),
            reasoning_config={"enabled": False, "effort": "none"},
            base_url="https://api.mistral.ai/v1",
        )
        assert kw.get("extra_body", {}).get("think") is None
        assert kw.get("reasoning_effort") == "none"
