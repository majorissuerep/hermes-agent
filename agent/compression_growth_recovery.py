"""Bounded anti-thrash probation after genuine appended context growth.

A refused/ineffective compaction judges the transcript at that boundary, not
every future transcript. Persist a fingerprinted baseline so meaningful new
material can earn one probe without resetting the guard on every turn/restart.
"""
from __future__ import annotations

import logging

from agent.usage_anchor import anchored_context_tokens, capture_usage_anchor

logger = logging.getLogger(__name__)
_KEY = "_compression_growth_baseline"


def _route(compressor) -> list:
    return [compressor.model, compressor.provider, compressor.base_url, compressor.context_length]


def record_growth_baseline(compressor, messages: list) -> None:
    database = getattr(compressor, "_session_db", None)
    session_id = getattr(compressor, "_session_id", "")
    if not session_id or database is None:
        return
    anchor = capture_usage_anchor(1, 0, messages)
    if anchor is None:
        return
    try:
        database.patch_session_model_config(session_id, {_KEY: {"route": _route(compressor), "anchor": anchor}})
    except Exception:
        logger.debug("Could not persist compaction growth baseline", exc_info=True)


def maybe_rearm_after_growth(compressor, messages: list) -> bool:
    count = getattr(compressor, "_ineffective_compression_count", 0)
    database = getattr(compressor, "_session_db", None)
    session_id = getattr(compressor, "_session_id", "")
    if not isinstance(count, int) or count < 2 or not session_id or database is None:
        return False
    try:
        baseline = database.get_session_model_config_value(session_id, _KEY)
        if not isinstance(baseline, dict) or baseline.get("route") != _route(compressor):
            return False
        anchor = baseline.get("anchor")
        if not isinstance(anchor, dict) or len(messages) < int(anchor.get("base_count", 0)) + 4:
            return False
        grown = anchored_context_tokens(messages, anchor)
        # Only new appended content pays for a probe: mixed rough/provider
        # estimates, display metadata, rewinds and tiny chat turns cannot.
        runway = max(1024, min(compressor.threshold_tokens // 2, 8192))
        if grown is None or grown - int(anchor["prompt_tokens"]) < runway:
            return False
        # Consume durable evidence before disarming. A failed write keeps the
        # breaker armed; another process must not repeatedly spend this probe.
        def consume(current):
            if current != baseline:
                raise ValueError("compaction growth evidence changed before consumption")

        if database.update_conversation_model_config_value(session_id, _KEY, consume) != session_id:
            return False
    except Exception:
        logger.debug("Could not validate/consume compaction growth baseline", exc_info=True)
        return False
    compressor._record_ineffective_compression_verdict(1)
    compressor._set_anti_thrash_recovery_deadline(0.0)
    logger.info("Context grew by ~%s tokens since the compaction boundary; allowing one recovery probe", grown - 1)
    return True
