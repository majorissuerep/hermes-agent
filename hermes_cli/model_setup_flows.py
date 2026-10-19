"""Per-provider model-selection wizard flows for ``hermes setup`` / ``hermes model``.

main / config / auth / models helpers are imported lazily inside bodies: avoids the main.py import
cycle and lets tests patch ``hermes_cli.config.load_config`` etc. at call time. The shared skeleton
lives in :mod:`hermes_cli.model_setup_flows_common`; custom transports have their own sibling.
"""

from __future__ import annotations

import contextlib
import argparse
import os

from hermes_cli.config import clear_model_endpoint_credentials
from hermes_cli.model_setup_flows_common import (
    _HTTP, _activate_provider_model, _ask, _commit_model_config, _curses_choice,
    _ensure_flow_api_key, _finish_model,
    _load_config_model_section, _models_dev_merged, _oauth_gate, _pick_model_or_prompt,
    _print_numbered, _prompt_auth_credentials_choice,
    _note_setup_failure, _say, _show_curated)


def _env_base_url(base_url_env: str) -> str:
    """Base-URL override from ``.env`` then the process environment ('' when unset)."""
    from hermes_cli.config import get_env_value
    if not base_url_env:
        return ""
    return get_env_value(base_url_env) or os.getenv(base_url_env, "")


def _prompt_base_url_override(effective_base: str, base_url_env: str, *, persist_env: bool = True) -> str:
    """Optional ``Base URL [...]`` prompt; a valid override is saved to *base_url_env*."""
    from hermes_cli.config import save_env_value
    override = _ask(f"Base URL [{effective_base}]: ", cancel_msg="", on_cancel="")
    if override and base_url_env:
        if not override.startswith(_HTTP):
            print("  Invalid URL — must start with http:// or https://. Keeping current value.")
        else:
            if persist_env:
                save_env_value(base_url_env, override)
            return override
    return effective_base


def _report_live_models(model_list, source: str) -> None:
    if model_list:
        print(f"  Found {len(model_list)} model(s) from {source}")


def _model_flow_ai_gateway(config, current_model=""):
    """Vercel AI Gateway provider: ensure API key, then pick model with pricing."""
    from hermes_constants import AI_GATEWAY_BASE_URL
    from hermes_cli.main_provider_setup import _prompt_api_key
    from hermes_cli.auth import PROVIDER_REGISTRY, _prompt_model_selection
    from hermes_cli.config import get_env_value
    pconfig = PROVIDER_REGISTRY["ai-gateway"]
    existing_key = get_env_value("AI_GATEWAY_API_KEY") or ""
    if not existing_key:
        _say("Create API key here: https://vercel.com/d?to=%2F%5Bteam%5D%2F%7E%2Fai-gateway&title=AI+Gateway",
             "Add a payment method to get $5 in free credits.", "")
    _resolved, abort = _prompt_api_key(pconfig, existing_key, provider_id="ai-gateway")
    if abort:
        return

    from hermes_cli.models import ai_gateway_model_ids
    from hermes_cli.models_pricing import get_pricing_for_provider
    models_list = ai_gateway_model_ids(force_refresh=True)
    pricing = get_pricing_for_provider("ai-gateway", force_refresh=True)
    selected = _prompt_model_selection(models_list, current_model=current_model, pricing=pricing)
    # Inline credentials are deliberately left untouched here (historical behavior).
    _finish_model(selected, "ai-gateway", f"Default model set to: {selected} (via Vercel AI Gateway)",
                  base_url=AI_GATEWAY_BASE_URL, api_mode="chat_completions", clear_creds=False)


def _model_flow_moa(config, current_model=""):
    """Mixture of Agents virtual provider: pick a preset (list always shown, even with one entry),
    persist it, print the breakdown. No credential step — presets reference configured providers."""
    from hermes_cli.auth import _save_model_choice
    from hermes_cli.moa_config import normalize_moa_config
    moa = normalize_moa_config(config.get("moa") if isinstance(config, dict) else {})
    presets = moa.get("presets") or {}
    if not presets:
        print("No MoA presets configured. Run `hermes moa configure <name>` first.")
        return

    names = list(presets.keys())
    default_name = moa.get("default_preset") or names[0]
    # Rows show the aggregator as the acting/billed model so the picker is informative before drilling in.
    rows = []
    for n in names:
        agg = presets[n].get("aggregator") or {}
        agg_label = f"{agg.get('provider')}:{agg.get('model')}" if agg else ""
        ref_count = len(presets[n].get("reference_models") or [])
        suffix = "  ← default" if n == default_name else ""
        rows.append(f"{n}  (acting: {agg_label}, {ref_count} refs){suffix}")
    default_idx = names.index(default_name) if default_name in names else 0

    title = "Select a Mixture of Agents preset:"
    idx = _curses_choice(title, rows, default_idx)
    if idx is None:
        _print_numbered(title, rows, default_idx)
        raw = _ask(f"  Choice [1-{len(rows)}]: ", raw=True, cancel_msg="No change.")
        if raw is None:
            return
        try:
            idx = default_idx if not raw else max(0, min(len(rows) - 1, int(raw) - 1))
        except ValueError:
            print("No change.")
            return
    if idx < 0:
        print("No change.")
        return

    selected_name = names[idx]
    cfg, model = _load_config_model_section()
    model["default"] = selected_name
    model["provider"] = "moa"
    # Virtual local provider: drop stale endpoint credentials AND base_url (which
    # clear_model_endpoint_credentials intentionally leaves alone).
    clear_model_endpoint_credentials(model, clear_api_mode=True)
    model.pop("base_url", None)
    _commit_model_config(cfg)
    _save_model_choice(selected_name)

    preset = presets[selected_name]
    _say(
        "",
        f"Default model set to: {selected_name} (via Mixture of Agents)",
        f"  Preset: {selected_name}",
        "  Reference models (advise once per user turn):",
    )
    for i, slot in enumerate(preset.get("reference_models") or [], start=1):
        print(f"    {i}. {slot.get('provider')}:{slot.get('model')}")
    agg = preset.get("aggregator") or {}
    print(
        f"  Aggregator:  {agg.get('provider')}:{agg.get('model')}  (acting model — runs every step and carries almost all of the cost)"
    )


def _model_flow_openai_codex(config, current_model=""):
    """OpenAI Codex provider: ensure logged in, then pick model."""
    from hermes_cli.auth import (
        get_codex_auth_status, _prompt_model_selection, _login_openai_codex, PROVIDER_REGISTRY, DEFAULT_CODEX_BASE_URL,
    )
    from hermes_cli.codex_models import get_codex_model_ids
    if not _oauth_gate(
        bool(get_codex_auth_status().get("logged_in")), "OpenAI Codex", _login_openai_codex, argparse.Namespace(),
        PROVIDER_REGISTRY["openai-codex"], recheck=lambda: get_codex_auth_status().get("logged_in")):
        return

    # Prefer the credential pool (where `hermes auth` stores device_code tokens),
    # fall back to legacy provider state.
    # Token and route base travel together (#121486): a pooled gateway key must never be sent to
    # the chatgpt.com default by the catalog probe.
    _codex_token = _codex_base = None
    with contextlib.suppress(Exception):
        _codex_status = get_codex_auth_status()
        if _codex_status.get("logged_in"):
            _codex_token, _codex_base = _codex_status.get("api_key"), _codex_status.get("base_url")
    if not _codex_token:
        with contextlib.suppress(Exception):
            from hermes_cli.auth import resolve_codex_runtime_credentials
            _creds = resolve_codex_runtime_credentials()
            _codex_token, _codex_base = _creds.get("api_key"), _creds.get("base_url")

    codex_models = get_codex_model_ids(access_token=_codex_token, base_url=_codex_base)
    selected = _prompt_model_selection(
        codex_models, current_model=current_model, confirm_provider="openai-codex",
        confirm_base_url=_codex_base or DEFAULT_CODEX_BASE_URL, confirm_api_key=_codex_token or "")
    _activate_provider_model(selected, "openai-codex", DEFAULT_CODEX_BASE_URL,
                             f"Default model set to: {selected} (via OpenAI Codex)")


def _model_flow_xai_oauth(_config, current_model="", *, args=None):
    """xAI Grok OAuth (SuperGrok / Premium+) provider: ensure logged in, then pick model."""
    from hermes_cli.auth import (
        get_xai_oauth_auth_status, _prompt_model_selection, resolve_xai_oauth_runtime_credentials, _login_xai_oauth,
        DEFAULT_XAI_OAUTH_BASE_URL, PROVIDER_REGISTRY)
    from hermes_cli.models import provider_model_ids
    login_args = argparse.Namespace(no_browser=bool(getattr(args, "no_browser", False)), timeout=getattr(args, "timeout", None))
    if not _oauth_gate(
        bool(get_xai_oauth_auth_status().get("logged_in")), "xAI Grok OAuth (SuperGrok / Premium+)", _login_xai_oauth,
        login_args, PROVIDER_REGISTRY["xai-oauth"], fresh_name="xAI OAuth"):
        return

    # ``resolve_xai_oauth_runtime_credentials`` only reads the auth.json singleton, but
    # credentials may live only in the pool (``hermes auth add xai-oauth``) — fall back to
    # the default base URL so the picker still completes.
    base_url = DEFAULT_XAI_OAUTH_BASE_URL
    with contextlib.suppress(Exception):
        creds = resolve_xai_oauth_runtime_credentials()
        base_url = (creds.get("base_url") or "").strip().rstrip("/") or base_url

    models = provider_model_ids("xai-oauth")
    selected = _prompt_model_selection(models, current_model=current_model or (models[0] if models else "grok-4.6"))
    _activate_provider_model(selected, "xai-oauth", base_url,
                             f"Default model set to: {selected} (via xAI Grok OAuth — SuperGrok / Premium+)")


def _select_zai_endpoint(current_base: str) -> str:
    """Picker for the official Z.AI endpoints (``ZAI_ENDPOINTS`` in ``hermes_cli.auth``, kept in
    sync with the probe list) plus a custom-proxy option. Returns the selected base URL;
    *current_base* on cancel/error."""
    from hermes_cli.main_provider_setup import _prompt_provider_choice
    from hermes_cli.auth import ZAI_ENDPOINTS
    options = [(label, url) for _, url, _, label in ZAI_ENDPOINTS]
    normalized_current = (current_base or "").strip().rstrip("/")

    # Default to the active endpoint when known; a custom URL defaults to "Custom proxy".
    default_idx = next((idx for idx, (_, url) in enumerate(options) if normalized_current == url.rstrip("/")),
                       len(options) if normalized_current else 0)
    choices = [f"{label} ({url})" for label, url in options] + ["Custom proxy URL"]
    selected = _prompt_provider_choice(choices, default=default_idx, title="Select Z.AI / GLM endpoint:")
    if selected is None:
        return current_base
    if selected != len(options):
        return options[selected][1].rstrip("/")
    override = _ask(f"Custom base URL [{current_base}]: ", cancel_msg="")
    if not override:
        return current_base
    if not override.startswith(_HTTP):
        print("  Invalid URL — must start with http:// or https://. Keeping current value.")
        return current_base
    return override.rstrip("/")


def _api_key_provider_model_list(provider_id: str, pconfig, existing_key: str, key_env: str, effective_base: str) -> list:
    """Model list for an API-key provider: models.dev registry (cached, agentic/tool-capable filter)
    → curated static list (offline insurance) → provider-owned catalog (``ProviderProfile.fetch_models``
    merged with ``fallback_models`` exactly like the ``/model`` picker's
    ``models._profile_live_catalog``; generic /models probe for unregistered providers).
    """
    from hermes_cli.config import get_env_value
    from hermes_cli.models import _PROVIDER_MODELS, fetch_api_models, probe_profile_catalog
    curated = _PROVIDER_MODELS.get(provider_id, [])
    api_key_for_probe = existing_key or (get_env_value(key_env) if key_env else "")

    # models.dev first (tool-capable, noise-filtered), merged with curated so newly added
    # models still appear.
    model_list = _models_dev_merged(provider_id, curated)
    if model_list:
        _report_live_models(model_list, "models.dev registry")
        return model_list
    if curated and len(curated) >= 8:
        # Substantial curated list — use it directly, skip live probe
        _show_curated(curated)
        return curated
    from providers import get_provider_profile
    profile = get_provider_profile(provider_id)
    if profile is not None:
        # The profile owns endpoint (models_url), headers and response shape. Same probe as the
        # ``/model`` picker; when neither live nor fallback_models yields rows, the curated
        # ``_PROVIDER_MODELS`` row still applies (built-in providers with a short curated list).
        model_list = probe_profile_catalog(provider_id, profile, api_key_for_probe, effective_base)
        if model_list:
            _report_live_models(model_list, f"{pconfig.name} catalog")
            return model_list
        _show_curated(curated)
        return list(curated)
    live_models = fetch_api_models(api_key_for_probe, effective_base)
    if live_models and len(live_models) >= len(curated):
        _report_live_models(live_models, f"{pconfig.name} API")
        return live_models
    _show_curated(curated)  # may be empty: falls through to raw input
    return curated


def _model_flow_api_key_provider(config, provider_id, current_model=""):
    """Generic flow for supported API-key providers and explicitly installed plugins."""
    from hermes_cli.auth import PROVIDER_REGISTRY
    from hermes_cli.config import save_env_value, load_config
    pconfig = PROVIDER_REGISTRY[provider_id]
    key_env = pconfig.api_key_env_vars[0] if pconfig.api_key_env_vars else ""
    base_url_env = pconfig.base_url_env_var or ""

    _, existing_key, abort = _ensure_flow_api_key(provider_id, pconfig)
    if abort:
        return

    # Optional base URL override. Precedence: env var → config.yaml model.base_url → registry
    # default; reading config.yaml keeps a saved remote URL from being overwritten with
    # localhost when the user just presses Enter.
    current_base = _env_base_url(base_url_env)
    if not current_base:
        with contextlib.suppress(Exception):
            _m = load_config().get("model") or {}
            if str(_m.get("provider") or "").strip().lower() == provider_id:
                current_base = str(_m.get("base_url") or "").strip()
    effective_base = current_base or pconfig.inference_base_url

    if provider_id == "zai":
        # Four official endpoints with separate billing paths — a picker lets users match
        # the endpoint to their key type.
        chosen_base = _select_zai_endpoint(effective_base)
        if chosen_base and chosen_base != effective_base and base_url_env:
            save_env_value(base_url_env, chosen_base)
        effective_base = chosen_base
    else:
        effective_base = _prompt_base_url_override(effective_base, base_url_env)

    model_list = _api_key_provider_model_list(provider_id, pconfig, existing_key, key_env, effective_base)

    # Per-model pricing when the provider supports it; get_pricing_for_provider() is memoized
    # and returns {} otherwise — never a blocking fetch beyond the catalog lookup above.
    pricing: dict = {}
    if model_list:
        try:
            from hermes_cli.models_pricing import get_pricing_for_provider
            pricing = get_pricing_for_provider(provider_id) or {}
        except Exception:
            pricing = {}
    selected = _pick_model_or_prompt(
        model_list, "Model name: ", current_model=current_model, pricing=pricing, confirm_provider=provider_id,
        confirm_base_url=effective_base, confirm_api_key=existing_key)
    _finish_model(
        selected, provider_id, f"Default model set to: {selected} (via {pconfig.name})", base_url=effective_base,
        drop_api_mode=True)


def _anthropic_authenticate() -> bool:
    """Interactive Anthropic auth (OAuth subscription or API key). False = flow must stop."""
    from hermes_cli.main_provider_setup import _run_anthropic_oauth_flow
    from hermes_cli.config import save_env_value, save_anthropic_api_key
    _say("", "  Choose authentication method:", "", "    1. Claude Pro/Max subscription (OAuth login)",
         "    2. Anthropic API key (pay-per-token)", "    3. Cancel", "")
    choice = _ask("  Choice [1/2/3]: ", raw=True, cancel_msg="")
    if choice is None:
        return False
    if choice == "1":
        return _run_anthropic_oauth_flow(save_env_value)
    if choice == "2":
        _say("", "  Get an API key at: https://platform.claude.com/settings/keys", "")
        api_key = _ask("  API key (sk-ant-...): ", secret=True, cancel_msg="")
        if api_key is None:
            return False
        if not api_key:
            print("  Cancelled.")
            return False
        save_anthropic_api_key(api_key, save_fn=save_env_value)
        print("  ✓ API key saved.")
        return True
    print("  No change.")
    return False


def _model_flow_anthropic(config, current_model=""):
    """Flow for Anthropic provider — OAuth subscription, API key, or Claude Code creds."""
    from hermes_cli.auth import get_anthropic_key
    from hermes_cli.models import _PROVIDER_MODELS

    # Check ALL credential sources
    existing_key = get_anthropic_key()
    cc_available = False
    with contextlib.suppress(Exception):
        from agent.anthropic_credentials import read_claude_code_credentials, is_claude_code_token_valid, _is_oauth_token
        cc_creds = read_claude_code_credentials()
        if cc_creds and is_claude_code_token_valid(cc_creds):
            cc_available = True

    # Stale-OAuth guard: an expired OAuth token with no valid cc_creds fallback is treated
    # as missing so the re-auth path is offered.
    existing_is_stale_oauth = bool(existing_key and _is_oauth_token(existing_key) and not cc_available)
    has_creds = (bool(existing_key) and not existing_is_stale_oauth) or cc_available
    needs_auth = not has_creds

    if has_creds:
        if existing_key:
            from hermes_cli.env_loader import format_secret_source_suffix
            from hermes_cli.auth import PROVIDER_REGISTRY

            # Surface which env var supplied the key so Bitwarden users see "(from Bitwarden)".
            source_suffix = ""
            for var in PROVIDER_REGISTRY["anthropic"].api_key_env_vars:
                if os.getenv(var, "").strip() == existing_key:
                    source_suffix = format_secret_source_suffix(var)
                    if source_suffix:
                        break
            print(f"  Anthropic credentials: {existing_key[:12]}... ✓{source_suffix}")
        elif cc_available:
            print("  Claude Code credentials: ✓ (auto-detected)")
        print()
        choice = _prompt_auth_credentials_choice("Anthropic credentials:")
        if choice == "reauth":
            needs_auth = True
        elif choice == "cancel":
            return
        # "use" (default): proceed to model selection with existing creds

    if needs_auth and not _anthropic_authenticate():
        return
    print()

    selected = _pick_model_or_prompt(
        _PROVIDER_MODELS.get("anthropic", []), "Model name (e.g., claude-sonnet-4-20250514): ",
        current_model=current_model, confirm_provider="anthropic")
    # Clear base_url: resolve_runtime_provider() always hardcodes Anthropic's URL, and a
    # stale value can contaminate other providers on a later switch.
    _finish_model(selected, "anthropic", f"Default model set to: {selected} (via Anthropic)", drop_base_url=True, drop_api_mode=True)


# ── Generic flow for plugin providers without a bespoke `_model_flow_*` ────────────────────────────
# The credential step is keyed by the profile's auth_type: each returns ``(base_url, api_key)`` or
# None when the picker must stop (the helper already printed why). ``api_key`` providers keep
# ``_model_flow_api_key_provider``; ``hermes_cli.main`` routes every registered profile missing
# from ``_PROVIDER_MODEL_FLOWS`` here, so an admitted plugin is never a silent no-op.

def _external_process_login_gate(profile, status) -> bool:
    """Logged in → say so (with plan). Logged out → run the CLI's own login on a TTY (it is
    browser/device based, so it must own the terminal), else print the instruction and stop."""
    import shlex
    import subprocess
    import sys

    if status["logged_in"]:
        plan = f" ({status['plan']})" if status.get("plan") else ""
        _say(f"  {profile.display_name} credentials: ✓{plan}", "")
        return True
    login = status.get("login_command")
    if not login or not sys.stdin.isatty():
        _say(f"  ✗ {status['detail']}")
        return False
    _say(f"  {status['detail'].split('.')[0]}.", f"  Starting `{shlex.join(login[-2:])}` (press Ctrl-C to cancel)...", "")
    try:
        subprocess.run(login, check=False)
    except (KeyboardInterrupt, OSError):
        print("Login cancelled or failed.")
        return False
    if not profile.setup_status()["logged_in"]:
        print("Login failed.")
        _note_setup_failure("auth")
        return False
    _say("", f"  {profile.display_name} credentials: ✓", "")
    return True


def _plugin_flow_external_process(provider_id: str, profile) -> tuple[str, str] | None:
    from hermes_cli.auth import get_external_process_provider_status, resolve_external_process_provider_credentials
    status = get_external_process_provider_status(provider_id)
    _say(f"  {profile.display_name or provider_id} delegates Hermes turns to a local `{status.get('command') or profile.process_command}` process.",
         f"  Command: {status.get('resolved_command') or status.get('command') or '(not found)'}",
         f"  Backend marker: {status.get('base_url') or profile.base_url}", "")
    try:
        creds = resolve_external_process_provider_credentials(provider_id)
    except Exception as exc:
        _say(f"  ⚠ {exc}")
        return None
    # A profile that can ask its CLI (``setup_status``) gates on login before anything is saved.
    probe = profile.setup_status()
    if probe is not None:
        if not probe["available"]:
            _say(f"  ✗ {probe['detail']}")
            return None
        if not _external_process_login_gate(profile, probe):
            return None
    return str(creds.get("base_url") or profile.base_url or ""), ""


def _plugin_flow_oauth(provider_id: str, profile) -> tuple[str, str] | None:
    from hermes_cli.auth import get_auth_status
    from hermes_cli.auth_plugin_providers import plugin_missing_auth_handler_error
    status = get_auth_status(provider_id)
    if not status.get("logged_in"):
        missing = plugin_missing_auth_handler_error(provider_id, "add")
        _say(f"  ⚠ Not signed in to {profile.display_name or provider_id}.",
             f"  {missing.code if missing else status.get('hint') or f'Run `hermes auth add {provider_id}` to sign in.'}")
        return None
    from agent.credential_pool import load_pool
    entry = load_pool(provider_id).select()
    base_url = (entry.runtime_base_url if entry else "") or status.get("base_url") or profile.base_url or ""
    return str(base_url), (entry.runtime_api_key if entry else "") or ""


_PLUGIN_FLOW_CREDENTIALS = {
    "external_process": _plugin_flow_external_process,
    "oauth_device_code": _plugin_flow_oauth,
    "oauth_external": _plugin_flow_oauth,
}


def _is_profile_plugin_flow_provider(provider_id: str) -> bool:
    """True when *provider_id* is a registered profile whose auth_type the generic plugin flow handles."""
    from hermes_cli.auth_plugin_providers import plugin_profile
    profile = plugin_profile(provider_id)
    return profile is not None and profile.auth_type in _PLUGIN_FLOW_CREDENTIALS


def _plugin_flow_live_rows(profile, api_key: str, base_url: str) -> tuple[list[str] | None, dict[str, str]]:
    """``(live ids, {id: note})``. ``discover_models()`` (the account's own annotated picker, e.g.
    ``usage credits``) wins when the profile implements it; otherwise the plain ``fetch_models()``."""
    with contextlib.suppress(Exception):
        rows = profile.discover_models()
        if rows:
            _say(f"  Models below come from your {profile.display_name or profile.name} account.", "")
            return [r["id"] for r in rows], {r["id"]: r["note"] for r in rows if r.get("note")}
    live = None
    with contextlib.suppress(Exception):
        live = profile.fetch_models(api_key=api_key or None, base_url=base_url or None)
    return live, {}


def _model_flow_plugin_provider(config, provider_id, current_model=""):
    """Generic ``hermes model`` flow for a registered plugin profile: credential step by auth_type,
    catalog via ``merge_profile_catalog`` (live ``fetch_models()`` ⊕ ``fallback_models``), persist."""
    from hermes_cli.auth_plugin_providers import plugin_profile
    from hermes_cli.models import merge_profile_catalog
    del config
    profile = plugin_profile(provider_id)
    creds = _PLUGIN_FLOW_CREDENTIALS[profile.auth_type](provider_id, profile)
    if creds is None:
        return
    base_url, api_key = creds
    live, notes = _plugin_flow_live_rows(profile, api_key, base_url)
    model_list = merge_profile_catalog(provider_id, profile, live) or []
    selected = _pick_model_or_prompt(
        model_list, "Model name: ", current_model=current_model, confirm_provider=provider_id,
        confirm_base_url=base_url, confirm_api_key=api_key, notes=notes)
    _finish_model(selected, provider_id, f"Default model set to: {selected} (via {profile.display_name or provider_id})",
                  base_url=base_url or None, api_mode=profile.api_mode or None)
