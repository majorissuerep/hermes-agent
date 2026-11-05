"""Measured prompt/output budgets reported by OpenAI-compatible relays."""
from __future__ import annotations

import re


_COMPLETION_SPLIT = re.compile(
    r'\((\d+)\s+(?:tokens\s+)?in (?:the messages|your prompt|the prompt)\s*[;,]\s*'
    r'(\d+)\s+(?:tokens\s+)?(?:in|for) the completion\)'
)
_RELAY_SPLIT = re.compile(
    r'prompt\s*\((\d+)\s+tokens\)\s*\+\s*max tokens\s*\((\d+)\)\s*'
    r'exceeds (?:the )?context\s*\((\d+)\)'
)


def completion_output_budget(error_lower: str) -> int | None:
    """Return measured room for output; an already-oversized prompt has none."""
    relay = _RELAY_SPLIT.search(error_lower)
    if relay:
        available = int(relay[3]) - int(relay[1])
        suggestion = re.search(r'max_tokens\s*\(at most\s+(\d+)\s+here\)', error_lower)
        if suggestion:
            available = min(available, int(suggestion[1]))
        return available if available > 0 else None
    split = _COMPLETION_SPLIT.search(error_lower)
    context = re.search(r'maximum context length is (\d+)', error_lower)
    if not split or not context:
        return None
    available = int(context[1]) - int(split[1])
    return available if available > 0 else None
