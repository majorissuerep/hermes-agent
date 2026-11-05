"""Lossless source partitioning and budgeted, verbatim context selection."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
import contextlib
import contextvars
from dataclasses import dataclass
import json
import math
import re

from agent.model_metadata import CHARS_PER_TOKEN, estimate_tokens_rough


_OUTPUT_RESERVATION: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "context_selection_output_reservation", default=None,
)


def normalize_retained_ratio(value: float) -> float:
    ratio = float(value)
    if not math.isfinite(ratio) or not 0 < ratio < 1:
        raise ValueError("compression.retained_ratio must be a finite fraction between zero and one")
    return ratio


@contextlib.contextmanager
def selection_output_reservation(tokens: int):
    token = _OUTPUT_RESERVATION.set(tokens)
    try:
        yield
    finally:
        _OUTPUT_RESERVATION.reset(token)


def selected_output_tokens() -> int | None:
    return _OUTPUT_RESERVATION.get()


def selection_call_kwargs() -> dict[str, int]:
    reservation = selected_output_tokens()
    return {"max_tokens": reservation} if reservation is not None else {}


def _bounded_word(word: str, budget: int) -> list[str]:
    pieces = []
    while word:
        low, high = 1, min(len(word), budget * 4)
        while low < high:
            middle = (low + high + 1) // 2
            if estimate_tokens_rough(word[:middle]) <= budget:
                low = middle
            else:
                high = middle - 1
        pieces.append(word[:low])
        word = word[low:]
    return pieces


def _split_sentence(sentence: str, budget: int) -> list[str]:
    units, current = [], ""
    for match in re.finditer(r"\S+\s*|\s+", sentence):
        for word in _bounded_word(match[0], budget):
            if current and estimate_tokens_rough(current + word) > budget:
                units.append(current)
                current = ""
            current += word
    if current:
        units.append(current)
    return units


def source_units(text: str, maximum_tokens: int = 512) -> tuple[str, ...]:
    """Prefer whole lines/sentences; split an oversized sentence at word boundaries.

    An indivisible word larger than the model budget is continued losslessly.
    Concatenating the returned units always reproduces the complete source.
    """
    if maximum_tokens < 1:
        raise ValueError("source-unit budget must be positive")
    units = []
    for line in text.splitlines(keepends=True):
        if estimate_tokens_rough(line) <= maximum_tokens:
            units.append(line)
            continue
        for sentence in re.split(r"(?<=[.!?])(?=\s)|(?<=[。！？])", line):
            if not sentence:
                continue
            units.extend([sentence] if estimate_tokens_rough(sentence) <= maximum_tokens
                         else _split_sentence(sentence, maximum_tokens))
    return tuple(units)


def numbered_source(units: tuple[str, ...]) -> str:
    return "".join(f"[{index}]{unit}" + ("" if unit.endswith("\n") else "\n")
                   for index, unit in enumerate(units, 1))


@dataclass(frozen=True)
class ContextChunk:
    units: tuple[str, ...]
    retained_tokens: int
    ordinal: int
    total: int

    @property
    def text(self) -> str:
        return "".join(self.units)

    def numbered(self) -> str:
        return numbered_source(self.units)


def weighted_budgets(total: int, count: int) -> list[int]:
    """Linear recency weights 1..N; rounding never exceeds the total budget."""
    if total < 0 or count < 1:
        raise ValueError("invalid retained-context budget")
    denominator = count * (count + 1) // 2
    result = [total * weight // denominator for weight in range(1, count + 1)]
    result[-1] += total - sum(result)
    return result


def _balanced_groups(units: tuple[str, ...], prefix: list[float], count: int,
                     capacity: int) -> list[tuple[str, ...]] | None:
    groups, start = [], 0
    for remaining in range(count, 0, -1):
        last = len(units) - remaining + 1
        limit = bisect_right(prefix, prefix[start] + capacity, start + 1, last + 1) - 1
        if limit <= start:
            return None
        target = prefix[start] + (prefix[-1] - prefix[start]) / remaining
        boundary = bisect_left(prefix, target, start + 1, limit + 1)
        candidates = {min(limit, boundary), max(start + 1, min(limit, boundary - 1))}
        end = min(candidates, key=lambda index: abs(prefix[index] - target))
        groups.append(units[start:end])
        start = end
    return groups if start == len(units) else None


def plan_context_chunks(text: str, context_length: int, retained_tokens: int, *,
                        output_reserve: int = 16384, prompt_overhead: int = 512) -> list[ContextChunk]:
    """Cover all input using fitting, approximately equal chunks and one output budget."""
    safety = max(64, context_length // 20)
    capacity = context_length - output_reserve - prompt_overhead - safety
    if capacity < 64 or retained_tokens < 1:
        raise ValueError("context window cannot fit the selector and its output reservation")
    units = source_units(text, min(512, max(16, capacity // 8)))
    if not units:
        return []
    prefix = [0.0]
    for unit in units:
        numbered = f"[{len(units)}]{unit}" + ("" if unit.endswith("\n") else "\n")
        # Round the complete prompt, not every short ASCII line independently.
        price = len(numbered) / CHARS_PER_TOKEN if numbered.isascii() else estimate_tokens_rough(numbered)
        prefix.append(prefix[-1] + price)
    count = max(1, math.ceil(prefix[-1] / capacity))
    while count <= len(units):
        groups = _balanced_groups(units, prefix, count, capacity)
        if groups is not None:
            budgets = weighted_budgets(retained_tokens, len(groups))
            return [ContextChunk(group, budgets[index], index + 1, len(groups))
                    for index, group in enumerate(groups)]
        count += 1
    raise ValueError("source boundaries cannot fit the available input budget")


def _selected_indices(content: str, count: int) -> list[int]:
    text = content.strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3]
    value = json.loads(text)
    if not isinstance(value, dict) or set(value) != {"ranges"}:
        raise ValueError("context selector must return only source ranges")
    ranges = value["ranges"]
    if not isinstance(ranges, list) or not ranges or len(ranges) > 128:
        raise ValueError("context selector returned an invalid range list")
    selected, seen = [], set()
    for pair in ranges:
        if (not isinstance(pair, list) or len(pair) != 2
                or any(type(index) is not int for index in pair)
                or not 1 <= pair[0] <= pair[1] <= count):
            raise ValueError("context selector returned an invalid source range")
        for index in range(pair[0] - 1, pair[1]):
            if index not in seen:
                selected.append(index)
                seen.add(index)
    return selected


def select_verbatim(chunk: ContextChunk, response: str) -> str:
    """Keep whole selected source units, priority first, then render chronologically."""
    remaining = chunk.retained_tokens
    selected = []
    for index in _selected_indices(response, len(chunk.units)):
        price = estimate_tokens_rough(chunk.units[index]) + 16
        if price <= remaining:
            selected.append(index)
            remaining -= price
    if not selected:
        raise ValueError("context selector retained no complete source unit within budget")
    selected.sort()
    result, previous = [], -2
    for index in selected:
        if index != previous + 1:
            result.append(f"\n[Source {chunk.ordinal}/{chunk.total}, line {index + 1}]\n")
        result.append(chunk.units[index])
        previous = index
    text = "".join(result)
    if estimate_tokens_rough(text) > chunk.retained_tokens:
        raise ValueError("selected context exceeds its retained budget")
    return text
