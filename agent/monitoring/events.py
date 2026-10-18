"""Typed local monitoring events.

Content-free local cron execution events — the only shapes the monitoring plane emits: no prompts, messages,
tool args/results, session history, or usage analytics. Field order is wire
order (``asdict``); never reorder.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, ClassVar, Dict, Optional


class _MonitoringEvent:
    __slots__ = ()
    EVENT: ClassVar[str]

    def to_dict(self) -> dict[str, Any]:
        return {"event": self.EVENT, **asdict(self)}






@dataclass(slots=True)
class CronExecutionEvent(_MonitoringEvent):
    """Content-free durable cron execution lifecycle projection."""
    EVENT: ClassVar[str] = "cron_execution"
    status: str
    job_key: str
    source: str = "unknown"
    duration_ms: Optional[int] = None
    delivery_outcome: Optional[str] = None
    error_class: Optional[str] = None
    ts_ns: int = field(default_factory=time.time_ns)


__all__ = ["CronExecutionEvent"]
