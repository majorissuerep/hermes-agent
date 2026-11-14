"""Token price of encrypted reasoning that is replayed on later requests.

Codex/xAI ``encrypted_content`` and Claude thinking ``signature`` are ciphertext, so their bytes are
not their cost; local estimates used to price them at 0 (#100611: a 5M-char native compaction
checkpoint read as 1.29M tokens). Use the reasoning count reported for the response as its replay
budget. Pricing it at 0 hid most of a long Codex/xAI session from the
compaction cut, so compression found "nothing to cut" while the bill sat over the window (#125920).

A turn's reasoning is priced at the count stamped on it (``message_metadata.stamp_reasoning_tokens``).
Compaction checkpoints stay at 0: they are cumulative state, priced only by real usage.
"""

from __future__ import annotations

from typing import Any

from agent.message_metadata import stamped_reasoning_tokens

def strip_opaque_replay_items(items: Any) -> Any:
    """``codex_reasoning_items`` with ``encrypted_content`` blanked; the ciphertext is priced separately
    (:func:`codex_reasoning_replay_tokens`), never by its bytes as text."""
    if not isinstance(items, list):
        return items
    return [
        {k: ("" if k == "encrypted_content" else v) for k, v in item.items()} if isinstance(item, dict) else item
        for item in items
    ]


def codex_reasoning_replay_tokens(msg: Any) -> int:
    """Billed input cost of the encrypted reasoning an assistant row (or a converted Responses
    ``reasoning`` input item) replays. Compaction checkpoints are excluded."""
    items = msg.get("codex_reasoning_items") if isinstance(msg, dict) and msg.get("role") == "assistant" else None
    if not isinstance(items, list):
        return 0
    has_reasoning = any(
        isinstance(item, dict) and item.get("type") == "reasoning"
        and isinstance(item.get("encrypted_content"), str) and item["encrypted_content"]
        for item in items)
    # Ciphertext length is not a portable token measure. Historical unstamped rows retain
    # the real-usage anchor; even a one-character carrier can have a known large replay cost.
    return stamped_reasoning_tokens(msg) if has_reasoning else 0


def signed_thinking_replay_tokens(msg: Any, readable_tokens: int) -> int:
    """Billed input cost of an Anthropic-signed thinking carrier a kept-thinking route replays: the
    provider-reported count when stamped (the server re-expands the FULL thinking from the
    signature), else the readable text's estimate (summary, or 0 for ``redacted_thinking``)."""
    return stamped_reasoning_tokens(msg) or readable_tokens
