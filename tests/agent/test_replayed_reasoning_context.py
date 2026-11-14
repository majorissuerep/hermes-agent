"""Context budgets use reported replay cost, independently of ciphertext size."""
from copy import deepcopy

import pytest

from agent.context_compressor import _estimate_msg_budget_tokens
from agent.model_metadata import estimate_messages_tokens_rough


@pytest.mark.parametrize("size", [1, 40_000])
def test_reported_replay_cost_reaches_both_context_budgets_without_changing_history(size):
    row = {"role": "assistant", "content": "answer", "codex_reasoning_items": [
        {"type": "reasoning", "encrypted_content": "x" * size}],
        "display_metadata": {"reasoning_tokens": 8_000}}
    plain = {"role": "assistant", "content": "answer"}
    original = deepcopy(row)
    for price in (lambda m: _estimate_msg_budget_tokens(m), lambda m: estimate_messages_tokens_rough([m])):
        assert 8_000 <= price(row) - price(plain) < 8_050
    assert row == original


def test_legacy_ciphertext_and_native_checkpoints_do_not_invent_reported_token_counts():
    base = {"role": "assistant", "content": "answer"}
    for kind in ("reasoning", "compaction"):
        row = {**base, "codex_reasoning_items": [{"type": kind, "encrypted_content": "x" * 40_000}]}
        if kind == "compaction":
            row["display_metadata"] = {"reasoning_tokens": 8_000}
        assert _estimate_msg_budget_tokens(row) - _estimate_msg_budget_tokens(base) < 50
        assert estimate_messages_tokens_rough([row]) - estimate_messages_tokens_rough([base]) < 50
