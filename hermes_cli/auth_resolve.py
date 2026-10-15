"""Inference provider selection policy, separate from auth storage and login protocols."""
from __future__ import annotations

from typing import Optional


def resolve_provider(
    requested: Optional[str] = None,
    *,
    explicit_api_key: Optional[str] = None,
    explicit_base_url: Optional[str] = None,
    skip_free_tier: bool = False) -> str:
    """Resolve explicit config first, then supported credentials and the active login.

    Never adopt an unrelated host credential or silently switch to a removed provider.
    ``skip_free_tier`` remains accepted for callers checking setup readiness.
    """
    from hermes_cli.auth import (
        AuthError, _plugin_aliases, _registry_lookup, _get_config_hint_for_unknown_provider,
        _config_model_provider, _scoped_key_env_reader, _logged_in_oauth_active_provider,
        _env_key_auto_detected, logger,
    )

    normalized = (requested or "auto").strip().lower()
    normalized = _plugin_aliases().get(normalized, normalized)

    if normalized == "custom" or normalized.startswith("custom:") or _registry_lookup(normalized) is not None:
        return normalized
    if normalized != "auto":
        hint = _get_config_hint_for_unknown_provider(normalized)
        tail = (f"\n\n{hint}" if hint else " Check 'hermes model' for available providers, "
                "or run 'hermes doctor' to diagnose config issues.")
        raise AuthError(f"Unknown provider '{normalized}'." + tail, code="invalid_provider")

    if explicit_api_key or explicit_base_url:
        return "custom" if explicit_base_url else "openai-api"

    _model_cfg, cfg_provider = _config_model_provider()
    if cfg_provider:
        return resolve_provider(cfg_provider)

    _scoped_key_env = _scoped_key_env_reader()
    # Determined up front so the env-key tier can warn when an exported key preempts it; the actual
    # OAuth fallback still happens after the env-key tier.
    _oauth_active = _logged_in_oauth_active_provider(skip_free_tier=skip_free_tier)
    env_pid = _env_key_auto_detected(_scoped_key_env, _oauth_active)
    if env_pid:
        return env_pid

    # Logged-in OAuth provider is a LAST-RESORT fallback (it used to sit above the env/config
    # checks, so a stale login silently overrode explicit intent).
    # Logged-in OAuth provider (auth.json `active_provider`) — a LAST-RESORT fallback, chosen only when the
    # user expressed no other preference above. Demoted here so explicit intent always wins. See #29285.
    if _oauth_active:
        if isinstance(_model_cfg, dict) and _model_cfg and not _model_cfg.get("provider"):
            logger.warning(
                "Provider resolved to logged-in OAuth provider %r because "
                "config.yaml `model` has no `provider` key. If you meant a "
                "different provider, set `model.provider` explicitly.",
                _oauth_active)
        return _oauth_active

    from hermes_constants import display_hermes_home
    raise AuthError(
        "Hermes is not connected to any AI provider yet. Run `hermes model` to pick one, type `/login` in chat, "
        f"or add a key with `hermes auth add <provider>`. (Advanced: put an API key such as ANTHROPIC_API_KEY in "
        f"{display_hermes_home()}/.env.)",
        code="no_provider_configured")

