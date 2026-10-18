"""Content-free metric values shared by local operational monitoring."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class Metric:
    name: str
    value: int | float
    attributes: dict[str, str]


def contains_any(*needles: str) -> Callable[[str], bool]:
    return lambda text: any(needle in text for needle in needles)


def safe_instance_id(raw: Any) -> str:
    value = str(raw or "unknown").encode("utf-8", errors="replace")
    return f"sha256:{hashlib.sha256(value).hexdigest()[:24]}"
