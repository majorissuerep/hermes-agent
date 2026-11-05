"""Oversized context is covered by fitting selectors, never sampled away."""
import copy
import json
import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.auxiliary_client import AuxiliaryExplicitCancellation
from agent.context_compressor import ContextCompressor
from agent.model_metadata import estimate_tokens_rough


def _response(content, finish_reason="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=content, refusal=None), finish_reason=finish_reason,
    )])


def _history():
    lines = [f"Recorded decision {index:05d}: keep the verified result.\n" for index in range(24000)]
    lines[12000] = "MIDDLE_UNIQUE_FACT: the original writer must stay revoked.\n"
    lines[-1] = "LATEST_UNIQUE_FACT: the current task is still active.\n"
    return [{"role": "user", "content": "Preserve the exact findings and outstanding task."},
            {"role": "assistant", "content": "".join(lines)}]


def _compressor():
    return ContextCompressor(model="synthetic-summary", provider="custom", quiet_mode=True,
                             config_context_length=250000, max_tokens=12288)


def _select(prompt):
    lines = re.findall(r"^\[(\d+)\](.*)$", prompt, re.MULTILINE)
    selected = [int(index) for index, text in lines
                if "UNIQUE_FACT" in text or "Preserve the exact" in text]
    if not selected:
        selected = [int(lines[0][0])] if lines else [1]
    return _response(json.dumps({"ranges": [[index, index] for index in selected]}))


@pytest.mark.parametrize("api_only", [False, True], ids=["transcript-text", "actual-api-content"])
def test_oversized_history_gets_balanced_fitting_requests_and_verbatim_middle_facts(api_only):
    compressor = _compressor()
    history = _history()
    if api_only:
        history[-1]["api_content"] = history[-1]["content"]
        history[-1]["content"] = "Earlier results are projected to the model."
    before = copy.deepcopy(history)
    requests = []

    def selector(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        requests.append(prompt)
        assert estimate_tokens_rough(prompt) + kwargs.get("max_tokens", 16384) <= 250000
        return _select(prompt)

    with patch("agent.context_compressor.call_llm", side_effect=selector):
        summary = compressor._generate_summary(history)
    assert len(requests) == 2
    assert "MIDDLE_UNIQUE_FACT: the original writer must stay revoked." in summary
    assert "LATEST_UNIQUE_FACT: the current task is still active." in summary
    assert all(f"Recorded decision {index:05d}:" in "".join(requests)
               for index in (0, 4000, 8000, 16000, 20000))
    assert estimate_tokens_rough(summary) <= 25000
    assert history == before


@pytest.mark.parametrize("past_user", [True, False], ids=["prior-user-request", "current-request-only"])
def test_short_overflowing_history_bypasses_message_floor_but_preserves_the_current_request(past_user):
    compressor = _compressor()
    history = _history() if past_user else _history()[1:]
    messages = [{"role": "system", "content": "Fixed system instructions."}, *history,
                {"role": "user", "content": "CURRENT REQUEST: finish the pending repair."}]
    before = copy.deepcopy(messages)
    with patch("agent.context_compressor.call_llm", side_effect=lambda **kwargs: _select(
        kwargs["messages"][0]["content"],
    )) as calls:
        result = compressor.compress(messages, current_tokens=330000)
    assert calls.call_count == 2
    text = "\n".join(row.get("content") or "" for row in result)
    assert "MIDDLE_UNIQUE_FACT: the original writer must stay revoked." in text
    assert "LATEST_UNIQUE_FACT: the current task is still active." in text
    assert "CURRENT REQUEST: finish the pending repair." in text
    assert "This session contains no user-authored turns." not in text
    assert messages == before
    assert estimate_tokens_rough(text) <= 25000


def test_actual_selector_window_rejection_replans_once_and_keeps_the_pinned_route():
    from agent.context_compressor import pin_summary_route

    compressor = _compressor()
    requests = []

    def selector(**kwargs):
        requests.append(kwargs)
        assert kwargs["provider"] == "custom-fallback"
        assert kwargs["model"] == "fallback-selector"
        if len(requests) == 1:
            raise ValueError("request exceeds the context window: prompt (200000 tokens) + "
                             "max tokens (16384) exceeds the context (131072)")
        assert estimate_tokens_rough(kwargs["messages"][0]["content"]) + kwargs["max_tokens"] <= 131072
        return _select(kwargs["messages"][0]["content"])

    with pin_summary_route({"provider": "custom-fallback", "model": "fallback-selector"}), patch(
        "agent.context_compressor.call_llm", side_effect=selector,
    ):
        summary = compressor._generate_summary(_history())
    assert 2 < len(requests) < 8
    assert "MIDDLE_UNIQUE_FACT: the original writer must stay revoked." in summary
    assert "LATEST_UNIQUE_FACT: the current task is still active." in summary
    assert compressor.context_length == 250000


def test_cancellation_between_chunks_preserves_the_previous_usable_checkpoint():
    compressor = _compressor()
    compressor._previous_summary = "trusted checkpoint"
    cancelled = False
    compressor._compression_cancelled_check = lambda: cancelled

    def selector(**kwargs):
        nonlocal cancelled
        cancelled = True
        return _select(kwargs["messages"][0]["content"])

    with patch("agent.context_compressor.call_llm", side_effect=selector) as calls:
        with pytest.raises(AuxiliaryExplicitCancellation):
            compressor._generate_summary(_history())
    assert calls.call_count == 1
    assert compressor._previous_summary == "trusted checkpoint"


def test_selector_output_ceiling_reduces_the_reservation_without_inventing_a_smaller_window():
    compressor = _compressor()
    requests = []

    def selector(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            raise ValueError("Range of max_tokens should be [1, 8192]")
        assert 0 < kwargs["max_tokens"] <= 8192
        return _select(kwargs["messages"][0]["content"])

    with patch("agent.context_compressor.call_llm", side_effect=selector):
        summary = compressor._generate_summary(_history())
    assert len(requests) == 3
    assert "LATEST_UNIQUE_FACT: the current task is still active." in summary
    assert compressor.context_length == 250000


@pytest.mark.parametrize("public", [False, True], ids=["summary-hook", "complete-compaction"])
def test_a_failed_later_chunk_never_publishes_an_earlier_partial_checkpoint(public):
    compressor = _compressor()
    compressor._previous_summary = "trusted earlier checkpoint"
    history = _history()
    if public:
        history = [{"role": "system", "content": "Fixed instructions."}, *history,
                   {"role": "user", "content": "Keep the current request intact."}]
    before = copy.deepcopy(history)
    calls = []

    def selector(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            return _response("{malformed ranges")
        return _select(kwargs["messages"][0]["content"])

    with patch("agent.context_compressor.call_llm", side_effect=selector):
        result = (compressor.compress(history, current_tokens=330000) if public
                  else compressor._generate_summary(history))
    assert result == before if public else result is None
    assert len(calls) == 2
    assert compressor._previous_summary == "trusted earlier checkpoint"
    assert history == before
