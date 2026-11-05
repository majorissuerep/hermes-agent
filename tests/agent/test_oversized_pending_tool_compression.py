"""An oversized just-loaded tool result must not wedge a short conversation."""
import copy
import json
import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.auxiliary_client import AuxiliaryExplicitCancellation
from agent.context_compressor import ContextCompressor
from agent.model_metadata import estimate_messages_tokens_rough


def _transcript():
    body = "Original source line.\n" * 60000 + "FINAL_FACT: use the verified filename.\n"
    return [
        {"role": "system", "content": "Fixed system instructions."},
        {"role": "user", "content": "Read the output and continue the current task."},
        {"role": "assistant", "content": None, "reasoning_content": "signed protocol reasoning",
         "tool_calls": [{"id": "read-current", "type": "function",
                         "function": {"name": "read_file", "arguments": '{"path":"synthetic.txt"}'}}]},
        {"role": "tool", "tool_call_id": "read-current", "content": body, "api_content": body},
    ]


def _compressor():
    return ContextCompressor(model="synthetic-summary", provider="custom", quiet_mode=True,
                             config_context_length=250000, max_tokens=12288)


def test_just_loaded_oversized_tool_is_compacted_without_rewriting_request_or_tool_identity():
    messages = _transcript()
    original = copy.deepcopy(messages)

    def selector(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        lines = re.findall(r"^\[(\d+)\](.*)$", prompt, re.MULTILINE)
        selected = next((int(index) for index, text in lines if "FINAL_FACT" in text), 1)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
            content=json.dumps({"ranges": [[selected, selected]]}), refusal=None,
        ))])

    compressor = _compressor()
    with patch("agent.context_compressor.call_llm", side_effect=selector) as calls:
        compressed = compressor.compress(messages, current_tokens=330000)
    assert calls.call_count == 2
    assert messages == original
    assert [{key: value for key, value in row.items() if key != "_compaction_tail"}
            for row in compressed[:3]] == original[:3]
    assert compressed[-1]["role"] == "tool"
    assert compressed[-1]["tool_call_id"] == "read-current"
    assert "FINAL_FACT: use the verified filename." in compressed[-1]["content"]
    assert "api_content" not in compressed[-1]
    assert estimate_messages_tokens_rough(compressed) <= 25000
    assert compressor._previous_summary is None


def test_parallel_pending_outputs_keep_the_exact_fallback_route_for_every_selection():
    from agent.context_compressor import pin_summary_route

    messages = _transcript()
    messages[2]["tool_calls"].append({"id": "read-second", "type": "function",
                                      "function": {"name": "read_file", "arguments": "{}"}})
    messages.append({**messages[-1], "tool_call_id": "read-second"})
    routes = []

    def selector(**kwargs):
        routes.append((kwargs.get("provider"), kwargs.get("model")))
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
            content='{"ranges":[[1,1]]}', refusal=None,
        ))])

    with pin_summary_route({"provider": "exact-fallback", "model": "fallback-selector"}), patch(
        "agent.context_compressor.call_llm", side_effect=selector,
    ):
        compressed = _compressor().compress(messages)
    assert len(routes) == 4
    assert set(routes) == {("exact-fallback", "fallback-selector")}
    assert [row["tool_call_id"] for row in compressed if row["role"] == "tool"] == ["read-current", "read-second"]


@pytest.mark.parametrize("cancel", [False, True], ids=["malformed-second-chunk", "cancelled-second-chunk"])
def test_pending_tool_selection_failure_or_cancellation_preserves_all_original_rows(cancel):
    messages = _transcript()
    original = copy.deepcopy(messages)
    compressor = _compressor()
    calls = []

    def selector(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2 and cancel:
            raise AuxiliaryExplicitCancellation()
        content = '{"ranges":[[1,1]]}' if len(calls) == 1 else '{"ranges":[[0,9000000]]}'
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
            content=content, refusal=None,
        ))])

    with patch("agent.context_compressor.call_llm", side_effect=selector):
        if cancel:
            with pytest.raises(AuxiliaryExplicitCancellation):
                compressor.compress(messages, current_tokens=330000)
        else:
            assert compressor.compress(messages, current_tokens=330000) == original
    assert len(calls) == 2
    assert messages == original
    assert compressor._previous_summary is None
