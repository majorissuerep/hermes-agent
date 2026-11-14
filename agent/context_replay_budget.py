"""Replay-aware tail prices for preserved native Anthropic thinking."""
from __future__ import annotations

from typing import Any


def native_anthropic_tail_prices(compressor: Any):
    """Tail prices ``messages -> [tokens per row]`` on a preserved-thinking native Anthropic route, else
    None. That route replays exactly the projected carriers minus this session's rejected signatures —
    what the preflight estimate prices — never the canonical ``reasoning`` keys. Priced over the whole
    list: whether a row's signed thinking survives conversion depends on its neighbours."""
    from agent.anthropic_thinking_policy import native_anthropic_preserves_prior_thinking

    if (getattr(compressor, "api_mode", "") or "") != "anthropic_messages" or not native_anthropic_preserves_prior_thinking(
        getattr(compressor, "base_url", ""), getattr(compressor, "model", "")
    ):
        return None
    from agent.anthropic_thinking_replay import session_rejected_thinking, strip_rejected_thinking
    from agent.context_compressor import _estimate_msg_budget_tokens
    from agent.message_sanitization import native_anthropic_row_costs

    rejected = session_rejected_thinking(
        compressor, getattr(compressor, "_session_db", None), getattr(compressor, "_session_id", "")
    )

    def prices(messages: list[dict[str, Any]]) -> list[int]:
        request_copies = [dict(m) if isinstance(m, dict) else m for m in messages]
        if rejected:
            for copy in request_copies:
                strip_rejected_thinking(copy, rejected)
        return [
            _estimate_msg_budget_tokens(shadow) + thinking if isinstance(shadow, dict) else 0
            for shadow, thinking in native_anthropic_row_costs(request_copies)
        ]

    return prices
