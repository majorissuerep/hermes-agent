"""Credential sections of `hermes status`, run through ``status._SECTIONS`` with its shared context.
Origin helpers (``_row``, ``_first_env_value``, ...) are resolved through the ``hermes_cli.status``
module object so tests that monkeypatch that module keep working."""

from datetime import datetime, timezone, UTC

from hermes_cli.nous_account import (
    format_nous_portal_entitlement_message, get_nous_portal_account_info)
from hermes_cli.nous_subscription import get_nous_subscription_features
from tools.tool_backend_helpers import managed_nous_tools_enabled
from hermes_cli import config
from hermes_time import safe_strftime


def _format_iso_timestamp(value) -> str:
    """Format ISO timestamps for status output, converting to local timezone."""
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return "(unknown)"
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except Exception:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return safe_strftime(parsed.astimezone(), "%Y-%m-%d %H:%M:%S %Z")


def _oauth_block(name: str, status: dict, hint: str, rows) -> None:
    """Print an OAuth provider row plus its conditional detail lines.

    ``rows`` are ``(label, status_key, formatter, gate)``: a detail prints when the raw value is
    truthy and ``gate`` is None or equals the logged-in state (False = only while logged out).
    """
    logged_in = bool(status.get("logged_in"))
    _status._row(name, logged_in, "logged in" if logged_in else f"not logged in (run: {hint})")
    for label, key, fmt, gate in rows:
        raw = status.get(key)
        if raw and (gate is None or gate == logged_in):
            _status._detail(label, fmt(raw) if fmt else raw)


# Values may be a single env var name (str) or a tuple of alternates (first found wins).
_TOOL_API_KEYS: dict[str, str | tuple[str, ...]] = {
    "Gemini speech": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "MiniMax speech": "MINIMAX_API_KEY", "MiniMax-CN speech": "MINIMAX_CN_API_KEY",
    "DeepInfra speech": "DEEPINFRA_API_KEY", "Firecrawl": "FIRECRAWL_API_KEY",
    "Tavily": "TAVILY_API_KEY", "Perplexity": "PERPLEXITY_API_KEY", "Keenable": "KEENABLE_API_KEY",
    "Browser Use": "BROWSER_USE_API_KEY",  # Optional — local browser works without this
    "Browserbase": "BROWSERBASE_API_KEY",  # Optional — direct credentials only
    "FAL": "FAL_KEY", "ElevenLabs": "ELEVENLABS_API_KEY", "GitHub": "GITHUB_TOKEN"}

# OAuth detail rows: (label, status key, formatter, gate) — see _oauth_block.
_FILE_REFRESH_ROWS = (
    ("Auth file:", "auth_store", None, None),
    ("Refreshed:", "last_refresh", _format_iso_timestamp, None), ("Error:", "error", None, False))

_OAUTH_BLOCKS = (
    # (row name, auth getter, login hint, detail rows)
    ("OpenAI Codex", "get_codex_auth_status", "hermes model", _FILE_REFRESH_ROWS),
    ("xAI OAuth", "get_xai_oauth_auth_status", "hermes auth add xai-oauth", _FILE_REFRESH_ROWS))

# Nous Tool Gateway per-feature state: first matching (predicate(feature, nous_auth), text(feature)).
_FEATURE_STATES = (
    (lambda f, _: f.managed_by_nous, lambda f: "active via Nous subscription"),
    (lambda f, _: f.active, lambda f: f"active via {f.current_provider or 'configured provider'}"),
    (lambda f, auth: f.included_by_default and auth, lambda f: "included by subscription, not currently selected"),
    (lambda f, auth: f.key == "modal" and auth, lambda f: "available via subscription (optional)"))


def _render_api_keys(ctx):
    _status._section("LLM API Keys")
    from hermes_cli.auth import PROVIDER_REGISTRY, get_anthropic_key
    # Anthropic uses the dedicated lookup (it also resolves OAuth tokens).
    for provider in PROVIDER_REGISTRY.values():
        if not provider.api_key_env_vars:
            continue
        value = get_anthropic_key() if provider.id == "anthropic" else _status._first_env_value(provider.api_key_env_vars)
        _status._row(provider.name, bool(value), config.redact_key(value))
    _status._section("Tool API Keys")
    for name, env_ref in _TOOL_API_KEYS.items():
        value = _status._first_env_value(env_ref)
        _status._row(name, bool(value), config.redact_key(value))


def _render_auth_providers(ctx):
    _status._section("Auth Providers")
    from hermes_cli import auth
    try:
        # Read-only display: the refresh-free snapshot, so `hermes status` never performs an OAuth
        # refresh or burns a single-use refresh token.
        nous_status = auth.get_nous_auth_status_local()
    except Exception:
        nous_status = {}
    statuses = {}
    for _, getter, _, _ in _OAUTH_BLOCKS:
        try:
            statuses[getter] = getattr(auth, getter)() or {}
        except Exception:
            statuses[getter] = {}

    info = None
    if any(nous_status.get(k) for k in ("logged_in", "access_token", "portal_base_url",
                                        "inference_credential_present", "error_code")):
        try:
            info = get_nous_portal_account_info()
        except Exception:
            pass
    ctx.nous_account_info = info
    ctx.nous_logged_in = logged_in = bool(nous_status.get("logged_in") or (info and info.logged_in))
    ctx.nous_inference_present = inference = bool(
        nous_status.get("inference_credential_present") or (info and info.inference_credential_present)
    )
    if nous_status.get("free_tier"):
        # Free tier: never rendered as an account login (no account ids, no refresh row).
        from hermes_cli.anon_auth import FREE_TIER_LABEL, GUEST_MODEL, UPGRADE_HINT
        _status._row("Nous Portal", True, f"{FREE_TIER_LABEL} · {GUEST_MODEL}")
        _status._detail("", UPGRADE_HINT)
        inference_url = nous_status.get("inference_base_url")
        if inference_url:
            _status._detail("Inference:", inference_url)
        for name, getter, hint, rows in _OAUTH_BLOCKS:
            _oauth_block(name, statuses.get(getter, {}), hint, rows)
        return
    nous_error = nous_status.get("error")
    _status._row("Nous Portal", logged_in,
         "logged in" if logged_in else "not logged in (Nous inference key configured)" if inference
         else "not logged in (run: hermes portal)")
    portal_url = nous_status.get("portal_base_url") or "(unknown)"
    inference_url = nous_status.get("inference_base_url") or (info.inference_base_url if info else None)
    for label, value, show in (
        ("Portal URL:", portal_url, logged_in or portal_url != "(unknown)" or nous_error),
        ("Inference:", inference_url, inference and inference_url),
        ("Access exp:", _format_iso_timestamp(nous_status.get("access_expires_at")),
         logged_in or nous_status.get("access_expires_at")),
        ("Key exp:", _format_iso_timestamp(nous_status.get("agent_key_expires_at")),
         logged_in or inference or nous_status.get("agent_key_expires_at")),
        ("Refresh:", "yes" if nous_status.get("has_refresh_token") else "no",
         logged_in or nous_status.get("has_refresh_token")),
        ("Error:", nous_error, nous_error)):
        if show:
            _status._detail(label, value)
    for name, getter, hint, rows in _OAUTH_BLOCKS:
        _oauth_block(name, statuses.get(getter, {}), hint, rows)


def _render_nous_gateway(ctx):
    if managed_nous_tools_enabled():
        features = get_nous_subscription_features(ctx.config)
        _status._section("Nous Tool Gateway")
        print("  Nous Portal   ✓ managed tools available" if features.nous_auth_present
              else "  Nous Portal   ✗ not logged in")
        for f in features.items():
            state = next((text(f) for match, text in _FEATURE_STATES if match(f, features.nous_auth_present)),
                         "not configured")
            _status._row(f.label, f.available or f.active or f.managed_by_nous, state, 15, " ")
    elif ctx.nous_logged_in or ctx.nous_inference_present:
        # Nous OAuth without entitlement, or an opaque inference key without Portal account
        # information, cannot enable the Tool Gateway.
        _status._section("Nous Tool Gateway")
        message = format_nous_portal_entitlement_message(
            ctx.nous_account_info, capability="managed web, image, TTS, STT, browser, and Modal tools"
        )
        for line in (message or "").splitlines():
            print(f"  {line}")


import hermes_cli.status as _status
