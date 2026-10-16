"""Already-summarized windows still admit useful old-tool pruning, without another summary call."""
from copy import deepcopy
import json

from agent.context_compressor import ContextCompressor, SUMMARY_PREFIX


def test_handoff_only_window_keeps_shrinking_tool_bodies_without_losing_turns_or_pairs(monkeypatch):
    compressor = ContextCompressor(model="acceptance-model", provider="custom", config_context_length=64_000,
                                   threshold_tokens_cap=12_000, protect_first_n=0, protect_last_n=4, quiet_mode=True)
    messages = [{"role": "user", "content": SUMMARY_PREFIX + "\nRetain continuity anchor. " * 250}]
    for turn in range(8):
        call_id = f"read-{turn}"
        if turn:
            messages.append({"role": "user", "content": f"Read reference {turn}"})
        messages.extend([
            {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "type": "function",
                "function": {"name": "read_file", "arguments": json.dumps({"path": f"reference-{turn}.txt"})}}]},
            {"role": "tool", "tool_call_id": call_id, "content": "old reference material " * 1100},
            {"role": "assistant", "content": f"Returned reference {turn}"},
        ])
    before = deepcopy(messages)

    def unexpected_summary(*args, **kwargs):
        raise AssertionError("an already-summarized empty window must not call the summary model")

    monkeypatch.setattr(compressor, "_generate_summary", unexpected_summary)
    # Pin the empty-handoff boundary; protocol E2E exercises natural window selection.
    monkeypatch.setattr(compressor, "_find_tail_cut_by_tokens", lambda *args: 1)
    result = compressor.compress(messages, current_tokens=55_000)
    assert messages == before
    assert len(json.dumps(result)) < len(json.dumps(before)) // 2
    assert result[0]["content"] == before[0]["content"]
    for role in ("user", "assistant"):
        assert [(m["content"], m.get("tool_calls")) for m in result if m["role"] == role] == [
            (m["content"], m.get("tool_calls")) for m in before if m["role"] == role]
    assert {m["tool_call_id"] for m in result if m["role"] == "tool"} == {
        call["id"] for m in result for call in m.get("tool_calls", [])}
    assert compressor._last_compression_made_progress
    assert compressor._last_compression_telemetry["method"] == "tool_result_prune"
