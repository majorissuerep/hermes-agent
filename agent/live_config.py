"""Settings that may take effect at the next local turn without rewriting its prefix."""

from __future__ import annotations

import time


COMPRESSION_CONFIG_KEYS = (
    "enabled", "progress_notices", "threshold", "model_thresholds", "threshold_tokens",
    "codex_gpt55_autoraise", "codex_app_server_auto", "codex_responses_native",
    "codex_responses_compact_threshold", "in_place", "checkpoint_required", "micro_compact",
    "micro_compact_every_n_turns", "micro_compact_defrag_threshold_tokens", "target_ratio",
    "tail_mode", "protect_last_n", "proactive_prune_tokens", "proactive_prune_min_result_chars",
    "proactive_prune_min_reclaim_tokens", "min_tail_user_messages", "idle_compact_after_seconds",
)


def compression_config_snapshot(config: dict | None) -> dict:
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    config = config if isinstance(config, dict) else {}
    compression = config.get("compression")
    compression = compression if isinstance(compression, dict) else {}
    defaults = DEFAULT_CONFIG.get("compression") or {}
    model = config.get("model")
    model = model if isinstance(model, dict) else {}
    return {"model.context_length": model.get("context_length", (DEFAULT_CONFIG.get("model") or {}).get("context_length")),
            **{f"compression.{key}": compression.get(key, defaults.get(key)) for key in COMPRESSION_CONFIG_KEYS}}


def apply_fallback_chain(agent, chain: list | None) -> None:
    """Refresh fallback routes, preserving an activated provider's live cooldown."""
    if agent is None:
        return
    if getattr(agent, "_fallback_activated", False) and (
        getattr(agent, "_rate_limited_until", 0) or 0
    ) > time.monotonic():
        return
    new_chain = list(chain or [])
    previous = list(getattr(agent, "_fallback_chain", []) or [])
    agent._fallback_chain = new_chain
    agent._fallback_model = new_chain[0] if new_chain else None
    if not getattr(agent, "_fallback_activated", False):
        agent._fallback_index = 0
    if previous != new_chain:
        getattr(agent, "_unavailable_fallback_keys", set()).clear()
