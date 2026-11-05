"""Hard-window recovery independent of ordinary head/tail message-count floors."""
from __future__ import annotations

from typing import Any

from agent.context_chunked_summary import _check_owner
from agent.context_oversized_tools import compress_pending_overflow
from agent.model_metadata import estimate_messages_tokens_rough


def _restore_scan(compressor: Any, scan: Any) -> None:
    from agent.conversation_compression import _caller_attempt_is_current

    if _caller_attempt_is_current(compressor):
        compressor._previous_summary = scan.previous_summary_before
        compressor._summary_has_user_turn = scan.has_user_turn_before


def compress_overflow_context(
    compressor: Any, messages: list[dict], current_tokens: int | None, focus: str | None,
    memory_context: str, bypass_cooldown: bool,
) -> list[dict] | None:
    """Hard overflow relaxes old-row protection, never the live request or protocol."""
    from time import monotonic
    from agent.context_compressor import _SUMMARY_ROUTE_PIN

    measured = estimate_messages_tokens_rough(messages)
    if max(current_tokens or 0, measured) + (compressor.max_tokens or 0) < compressor.context_length:
        return None
    route = _SUMMARY_ROUTE_PIN.get()
    if isinstance(route, dict) and route.get("deterministic") is True:
        return None  # Preserve the existing explicitly admitted last-resort fallback.
    pending = compress_pending_overflow(compressor, messages, current_tokens, focus, bypass_cooldown)
    if pending is not None:
        return pending
    head_end = 1 if messages and messages[0].get("role") == "system" else 0
    tail_start = compressor._find_last_user_message_idx(messages, 0)
    if tail_start < 0:
        tail_start = len(messages) - 1
    tail_start = compressor._ensure_last_n_user_messages_in_tail(
        messages, tail_start, head_end, compressor.min_tail_user_messages,
    )
    tail_start = compressor._align_boundary_backward(messages, tail_start)
    if tail_start <= head_end or any(row.get("role") == "system" for row in messages[head_end:tail_start]):
        return None  # An indivisible current request cannot be replaced by a history checkpoint.
    outside = max(0, (current_tokens or 0) - measured)
    fixed = estimate_messages_tokens_rough(messages[:head_end] + messages[tail_start:]) + outside
    if fixed + (compressor.max_tokens or 0) + 512 >= compressor.context_length:
        return None
    if monotonic() < compressor._summary_failure_cooldown_until and not bypass_cooldown:
        compressor._last_compress_aborted = True
        return messages
    _check_owner(compressor)
    compressor._active_compression_telemetry["hard_overflow"] = True
    scan = compressor._scan_window_handoffs(messages, head_end, tail_start, messages[head_end:tail_start])
    compressor._record_compression_regions(
        head_messages=messages[:head_end], middle_messages=scan.turns_to_summarize,
        tail_messages=messages[tail_start:],
    )
    try:
        summary = compressor._summarize_window(
            messages, scan.turns_to_summarize, scan, focus, memory_context, bypass_cooldown,
        )
    except BaseException:
        _restore_scan(compressor, scan)
        raise
    if summary is None:
        _restore_scan(compressor, scan)
        _check_owner(compressor)
        compressor._last_compress_aborted = True
        return messages
    candidate = compressor._assemble_compressed(messages, head_end, tail_start, scan, summary)
    _check_owner(compressor)
    return compressor._finalize_compressed(
        candidate, messages, len(messages), bool(compressor._spared_pending_tool_round(messages)),
    )
