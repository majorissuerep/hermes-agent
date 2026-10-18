"""Opt-in bounded lifecycle metrics for local scheduled sessions."""

from __future__ import annotations

import contextvars

import logging

import threading

from collections import OrderedDict

from concurrent.futures import Future, ThreadPoolExecutor

from datetime import datetime, timezone, UTC

from typing import Any, Callable

logger = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None

_executor_lock = threading.Lock()

def _submit(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future | None:
    """Fire-and-forget: run ``fn`` on the metrics worker in the caller's context (profile scope)."""
    global _executor
    try:
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hermes-session-metrics")
        return _executor.submit(contextvars.copy_context().run, _guarded, fn, *args, **kwargs)
    except Exception:  # interpreter shutdown refuses new work: the metric is simply dropped
        logger.debug("Session shared metric not scheduled", exc_info=True)
        return None

def _guarded(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    try:
        fn(*args, **kwargs)
    except Exception:
        logger.debug("Session shared metric not recorded", exc_info=True)

def drain(timeout: float = 5.0) -> None:
    """Wait for queued recordings (tests and live probes; the worker is FIFO)."""
    future = _submit(lambda: None)
    if future is not None:
        future.result(timeout=timeout)

def _emit(mark_name: str, build: Callable[..., dict[str, str] | None], **raw: Any) -> None:
    from . import shared_metrics_contract as contract
    from .shared_metrics_events import _emit as emit

    emit(getattr(contract, mark_name), build, **raw)

_CRON_NOTES_MAX = 512

_cron_delivery: OrderedDict[str, str] = OrderedDict()

_cron_skipped: set[str] = set()

_cron_lock = threading.Lock()

_LOCAL_DELIVERY = frozenset({"local", ""})

def cron_delivery_kind(job: Any) -> str:
    """Where a job's output goes, as a closed kind; targets themselves never leave the machine."""
    if not isinstance(job, dict):
        return "other"
    deliver = job.get("deliver")
    parts = deliver if isinstance(deliver, (list, tuple)) else str(deliver or "").split(",")
    kinds = {str(p).strip().lower().split(":", 1)[0] for p in parts} - {""}
    if not kinds or kinds <= _LOCAL_DELIVERY:
        return "local"
    if kinds & {"none", "off"}:
        return "none"
    return "webhook" if kinds == {"webhook"} else "platform"

def note_cron_execution(job: Any) -> None:
    """Remember the delivery kind of an execution about to run (or be refused) in this process."""
    execution_id = job.get("execution_id") if isinstance(job, dict) else None
    if not execution_id:
        return
    with _cron_lock:
        _cron_delivery[str(execution_id)] = cron_delivery_kind(job)
        while len(_cron_delivery) > _CRON_NOTES_MAX:
            _cron_delivery.popitem(last=False)

def note_cron_skipped(job: Any) -> None:
    """A gate (pre-run script ``wakeAgent=false`` / no output) decided this run does no agent work."""
    execution_id = job.get("execution_id") if isinstance(job, dict) else None
    if execution_id:
        with _cron_lock:
            if len(_cron_skipped) < _CRON_NOTES_MAX:
                _cron_skipped.add(str(execution_id))

def _iso_seconds(start: Any, end: Any) -> float:
    try:
        return max(0.0, (datetime.fromisoformat(str(end)) - datetime.fromisoformat(str(start))).total_seconds())
    except (TypeError, ValueError):
        return 0.0

def cron_run_fields(*, outcome: str, delivery_kind: str, seconds: float) -> dict[str, str]:
    from . import shared_metrics_contract as contract

    return {
        "delivery_kind": delivery_kind if delivery_kind in contract.CRON_DELIVERY_KINDS else "other",
        "duration_bucket": contract.duration_bucket(int(seconds * 1000)),
        "outcome": outcome if outcome in contract.CRON_RUN_OUTCOMES else "failed",
    }

def record_cron_finish(record: Any, delivery_outcome: Any = None) -> None:
    """One execution reached its (write-once) terminal row. A row that never started running was
    refused before any side effect (claim lost, dispatch limit, executor shutdown): ``skipped``,
    unless a failure notice was delivered for it."""
    if not isinstance(record, dict) or not record.get("id"):
        return
    execution_id = str(record["id"])
    with _cron_lock:
        delivery_kind = _cron_delivery.pop(execution_id, "other")
        gated = execution_id in _cron_skipped
        _cron_skipped.discard(execution_id)
    started = record.get("started_at")
    success = record.get("status") == "completed"
    if gated or (not started and not success and delivery_outcome is None):
        outcome = "skipped"
    else:
        outcome = "success" if success else "failed"
    _submit(_emit, "CRON_RUN_MARK", cron_run_fields, outcome=outcome, delivery_kind=delivery_kind,
            seconds=_iso_seconds(started, record.get("finished_at")) if started else 0.0)

def record_cron_missed(job: Any) -> None:
    """A scheduled occurrence the scheduler dropped without running it (catch-up disabled after
    downtime, or a one-shot past its grace window)."""
    _submit(_emit, "CRON_RUN_MARK", cron_run_fields, outcome="missed",
            delivery_kind=cron_delivery_kind(job), seconds=0.0)
