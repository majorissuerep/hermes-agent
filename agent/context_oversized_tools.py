"""Overflow recovery for text tool results the model has not answered yet."""
from __future__ import annotations

import logging
import time
from typing import Any

from agent.auxiliary_client import AuxiliaryExplicitCancellation
from agent.context_chunked_summary import _check_owner, select_context_text
from agent.context_chunking import weighted_budgets
from agent.model_metadata import estimate_messages_tokens_rough, estimate_tokens_rough


_NOTICE = "[Compacted tool output; complete original remains in session history.]\n"
logger = logging.getLogger(__name__)


def _pending_budget(compressor: Any, messages: list[dict], indices: list[int], current_tokens: int) -> int:
    skeleton = [dict(message) for message in messages]
    for index in indices:
        skeleton[index]["content"] = _NOTICE
        skeleton[index].pop("api_content", None)
    measured = estimate_messages_tokens_rough(messages)
    outside = max(0, current_tokens - measured)
    fixed = estimate_messages_tokens_rough(skeleton) + outside
    hard = compressor.context_length - (compressor.max_tokens or 0) - fixed - 256
    target = int(compressor.context_length * compressor.retained_ratio) - fixed - 64
    return min(hard, max(256 * len(indices), target))


def compress_pending_overflow(
    compressor: Any, messages: list[dict], current_tokens: int | None, focus: str | None,
    bypass_cooldown: bool,
) -> list[dict] | None:
    """Rewrite only oversized result bodies; keep the active request/calls/IDs intact.

    None means no eligible pending overflow. Failed selection returns the original
    transcript, never a partial result or a deterministic replacement for missing data.
    """
    from agent.context_compressor import (
        _COMPACTION_TAIL_MARKER, _fresh_compaction_message_copy, _pending_tool_round,
        _redact_compaction_text, _short_error_text, pin_summary_route, take_pinned_summary_route,
    )
    from agent.turn_context import drop_stale_api_content

    measured = estimate_messages_tokens_rough(messages)
    if max(current_tokens or 0, measured) + (compressor.max_tokens or 0) < compressor.context_length:
        return None
    indices = [index for index in _pending_tool_round(messages)
               if isinstance(messages[index].get("content"), str)]
    if not indices:
        return None
    budget = _pending_budget(compressor, messages, indices, current_tokens or 0)
    if budget < len(indices) * 64:
        return None  # The immutable request alone needs normal history compaction first.
    allowances = weighted_budgets(budget, len(indices))
    if all(estimate_tokens_rough(messages[index]["content"]) <= allowance
           for index, allowance in zip(indices, allowances)):
        return None
    if time.monotonic() < compressor._summary_failure_cooldown_until and not bypass_cooldown:
        compressor._last_compress_aborted = True
        return messages
    candidate = [_fresh_compaction_message_copy(message) for message in messages]
    pinned = take_pinned_summary_route()
    try:
        for index, allowance in zip(indices, allowances):
            body = messages[index]["content"]
            if estimate_tokens_rough(body) <= allowance:
                continue
            with pin_summary_route(pinned):
                selected = select_context_text(compressor, _redact_compaction_text(body), allowance, focus=focus or "")
            candidate[index]["content"] = _NOTICE + selected
            drop_stale_api_content(candidate[index])
        _check_owner(compressor)
        if estimate_messages_tokens_rough(candidate) >= measured:
            raise ValueError("tool-output selection did not reclaim context")
    except AuxiliaryExplicitCancellation:
        raise
    except Exception as error:
        _check_owner(compressor)
        logger.debug("Pending tool-output selection failed", exc_info=True)
        compressor._last_compress_aborted = True
        compressor._last_summary_error = _redact_compaction_text(_short_error_text(error))
        compressor._last_summary_selection_failure = True
        compressor._record_compression_failure_cooldown(30, compressor._last_summary_error)
        return messages
    for message in candidate:
        message[_COMPACTION_TAIL_MARKER] = True
    compressor._clear_terminal_summary_failures()
    compressor._clear_compression_failure_cooldown()
    compressor._last_summary_error = None
    telemetry = compressor._active_compression_telemetry
    telemetry.update(method="verbatim_range_selection", failure_class=None)
    return compressor._finalize_compressed(
        candidate, messages, len(messages), bool(compressor._spared_pending_tool_round(messages)),
    )
