"""In-place model switches, including cache-preserving automatic routing."""

import logging
from agent import agent_runtime_helpers as runtime

logger = logging.getLogger(__name__)


def switch_model(
    agent, new_model, new_provider, api_key='', base_url='', api_mode='', capabilities=None,
    *, preserve_prompt=False,
):
    """Switch the model/provider in-place for a live agent (rebuild clients, caching flags,
    compressor). Mirrors ``_try_activate_fallback()`` but also updates ``_primary_runtime`` so
    the change persists across turns. A failed swap/rebuild rolls back to the pre-switch
    snapshot and re-raises (callers catch)."""
    old_model = agent.model
    old_provider = agent.provider
    # ── Reload credential pool for the new provider (issue #52727) ── Without this,
    # ``recover_with_credential_pool`` sees a ``pool.provider != agent.provider`` mismatch and
    # short-circuits, leaving the new provider with no rotation/recovery on 401/429 and burning the original
    # pool's entries. Only reload when the provider actually changed (or the pool was missing) —
    # re-selecting the same provider must not churn the pool reference. A reload failure is logged +
    # swallowed: the switch itself must still complete.
    old_norm = (old_provider or "").strip().lower()
    new_norm = (new_provider or "").strip().lower()
    api_mode, base_url, destination_capabilities = runtime._resolve_switch_destination(
        agent, new_model, new_provider, base_url, api_mode, capabilities, old_norm, new_norm
    )
    snapshot = runtime._snapshot_switch_state(agent)
    try:
        runtime._swap_switch_runtime(
            agent, new_model, new_provider, api_key, base_url, api_mode, old_provider, old_norm, new_norm
        )
    except Exception:
        runtime._restore_switch_snapshot(agent, snapshot)
        raise
    custom_providers, effective_context_length = runtime._resolve_switch_context_length(agent, snapshot)
    # Refresh the custom-provider snapshot from the config just loaded so the prompt_caching lookup
    # sees flags added to config.yaml after session start.
    if custom_providers is not None:
        agent._custom_providers = custom_providers
    agent._use_prompt_caching, agent._use_native_cache_layout = agent._anthropic_prompt_cache_policy(
        provider=new_provider, base_url=agent.base_url, api_mode=api_mode, model=new_model
    )
    if hasattr(agent, "context_compressor") and agent.context_compressor:
        runtime._update_switch_compressor(agent, custom_providers, effective_context_length, snapshot)
    # Re-read the per-model reasoning_effort override so it applies immediately (per-model > global;
    # YAML False = disabled).
    try:
        from hermes_constants import resolve_reasoning_config
        from hermes_cli.config import load_config as _sm_load_config
        agent.reasoning_config = resolve_reasoning_config(_sm_load_config() or {}, agent.model)
        logger.info(
            "switch_model: reasoning_config resolved for %s: %s", agent.model, agent.reasoning_config
        )
    except Exception as _reasoning_err:
        logger.debug("switch_model: could not re-resolve reasoning_config: %s", _reasoning_err)
    # Automatic routing preserves the session's prompt and tool arrays; explicit picks
    # keep the existing next-turn prompt rebuild behavior.
    if not preserve_prompt:
        agent._cached_system_prompt = None
    # Publish the destination capability map only after every runtime setup above has succeeded.
    # Failed switches must leave the old map intact.
    agent.runtime_capabilities = destination_capabilities
    # Reset the cross-turn stale-call circuit breaker; otherwise the latched streak keeps
    # short-circuiting the freshly selected healthy provider.
    from agent.chat_completion_helpers import _reset_stale_streak
    _reset_stale_streak(agent)
    agent._primary_runtime = runtime._build_primary_runtime_snapshot(agent, api_mode)
    runtime._finish_switch(agent, new_provider, old_norm, new_norm)
    logger.info(
        "Model switched in-place: %s (%s) -> %s (%s)",
        old_model, old_provider, new_model, new_provider,
    )
    runtime._persist_switch_billing_route(agent)
