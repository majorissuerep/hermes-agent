"""Cron job argument normalization, validation and result shaping (re-exported by
tools/cronjob_tools.py)."""

import contextlib
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union

from cron.jobs import effective_job_state

import hermes_time

# Logger parity with the origin module.
logger = logging.getLogger("tools.cronjob_tools")

# A one-shot firing within this many minutes is still part of the conversation that created it:
# with platforms.slack.extra.reply_in_thread at its default true, the whole exchange under a
# top-level message lives in the thread keyed on that message's own id.
_THREAD_HORIZON_MINUTES = 60








def _mode_guidance_notes(job: dict[str, Any], user_deliver: Optional[str]) -> list[str]:
    """Mode guidance echoed once in the create/update response (not in the schema, which is
    paid for on every API call)."""
    notes: list[str] = []
    if job.get("monitor_script") or job.get("monitor_url"):
        notes.append(
            "Monitor mode: the source runs first each tick and its output is "
            "hashed as exact bytes — unchanged output suppresses the agent run "
            "(silent no_change tick), changed output injects a MONITOR CHANGE "
            "DETECTED diff into the prompt. The first tick always runs as "
            "baseline. The source must emit STABLE output (no timestamps, no "
            "random ordering) or every tick will look changed.")
    if job.get("no_agent"):
        notes.append(
            "no_agent mode: stdout is delivered verbatim; EMPTY stdout sends "
            "nothing at all (watchdog pattern — script should stay quiet when "
            "there is nothing to report). Non-zero exit or timeout sends an "
            "error alert. prompt/skills are ignored.")
    return notes


def _split_monitor_arg(
    monitor: Optional[str],
    monitor_script: Optional[str],
    monitor_url: Optional[str]) -> tuple:
    """Resolve the model-facing ``monitor`` field into the stored ``(monitor_script,
    monitor_url)`` pair. http(s):// is a URL, anything else a script path (a legal script path
    never starts with a URL scheme). None = unchanged, '' = clear; setting one source clears
    the other so switching transports never trips mutual exclusion; an explicit ``monitor``
    wins over the legacy alias fields."""
    if monitor is None:
        return monitor_script, monitor_url
    value = monitor.strip()
    if not value:
        return "", ""
    if value.lower().startswith(("http://", "https://")):
        return "", value
    return value, ""


def _repeat_display(job: dict[str, Any]) -> str:
    rep = job.get("repeat") or {}
    times, completed = rep.get("times"), rep.get("completed", 0)
    if times is None:
        return "forever"
    if times == 1:
        return "once" if completed == 0 else "1/1"
    return f"{completed}/{times}" if completed else f"{times} times"


def _clean_str_list(items: Any) -> list[str]:
    """Stripped, non-empty ``str(item)`` values from a str-or-iterable (order kept)."""
    if items is None:
        return []
    if isinstance(items, str):
        items = [items]
    return [s for s in (str(i).strip() for i in items) if s]


def _canonical_skills(skill: Optional[str] = None, skills: Optional[Any] = None) -> list[str]:
    if skills is None:
        skills = [skill] if skill else []
    elif isinstance(skills, str):
        skills = [skills]
    # `item or ""`: a None entry must drop out, not stringify to "None".
    return list(dict.fromkeys(_clean_str_list(item or "" for item in skills)))


def _normalize_optional_job_value(value: Optional[Any], *, strip_trailing_slash: bool = False) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if strip_trailing_slash:
        text = text.rstrip("/")
    return text or None


def _normalize_deliver_param(value: Any) -> Optional[str]:
    """Canonical string form of ``deliver``; None for None/empty. MCP clients may pass a list
    (``["telegram"]``) which the scheduler's ``str(deliver).split(",")`` would mangle."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return ",".join(_clean_str_list(value)) or None
    return str(value).strip() or None






def _same_origin(base_url: str, configured: str) -> bool:
    """Origin (scheme, host, effective port) equality, as for any bearer secret moved to a
    new URL: a host-only or subdomain match would let a job send the stored key over plain
    HTTP, to another port, or to a host the operator never configured."""
    from utils import base_url_origin
    try:
        want, got = base_url_origin(configured), base_url_origin(base_url)
    except ValueError:  # malformed URL (e.g. unclosed IPv6 bracket) cannot match
        return False
    return bool(want[1]) and got == want


def _base_url_refused(bu: str, prov: str, why: str) -> str:
    """The one refusal wording for a base_url override; ``why`` names the endpoint rule."""
    return f"base_url {bu!r} is not allowed for provider {prov!r}. {why}"


def _custom_stored_key_error(bu: str) -> Optional[str]:
    """Bare 'custom' is BYOK only while the runtime attaches no stored key. The resolver picks
    the host-gated env keys by HOSTNAME, so a base_url that would receive one must be an origin
    the operator or the provider registry names; pool and ``model.key_env`` keys already match
    their configured URL exactly."""
    from agent.secret_scope import get_secret_str
    from hermes_cli import runtime_provider as rp
    from hermes_cli.auth import PROVIDER_REGISTRY
    from hermes_constants import OPENROUTER_BASE_URL
    from utils import base_url_origin

    custom_why = (
        "A stored API key matches its hostname, and a stored credential may only be sent to a "
        "configured endpoint (same scheme, host and port); configure the endpoint as a custom "
        "provider to use it.")
    try:  # a URL urlparse rejects (unclosed IPv6 bracket) would raise in the key lookup below
        base_url_origin(bu)
    except ValueError:
        return _base_url_refused(bu, "custom", "It is not a valid URL.")
    if not any(rp.has_usable_secret(key) for key in rp._host_gated_env_key_candidates(bu, ollama=True)):
        return None
    # The RAW configured model.base_url: _get_model_config() may network-probe a local model.
    cfg = rp.load_config()
    model_cfg = cfg.get("model")
    configured = [
        model_cfg.get("base_url") if isinstance(model_cfg, dict) else None,
        get_secret_str("OPENAI_BASE_URL", ""), OPENROUTER_BASE_URL,
        # PROVIDER_REGISTRY already carries the direct OpenAI origin (openai-api).
        *(getattr(p, "inference_base_url", "") for p in PROVIDER_REGISTRY.values()),
        *(entry.get("base_url") for entry in rp.get_compatible_custom_providers(cfg)),
    ]
    if any(_same_origin(bu, str(url)) for url in configured if url):
        return None
    return _base_url_refused(bu, "custom", custom_why)


def _validate_cron_base_url(
    provider: Optional[Any], base_url: Optional[Any]) -> Optional[str]:
    """Reject pairing a stored credential with an off-origin base_url (a prompt-injected job
    could exfil the key). Allowed: no override; bare 'custom' while no stored key would go with
    it, or at a configured origin; an override with the same origin as the named provider's
    own endpoint. Everything else fails closed. Returns an error string if blocked, else None."""
    bu = _normalize_optional_job_value(base_url, strip_trailing_slash=True)
    if not bu:
        return None
    prov = _normalize_optional_job_value(provider)
    if not prov:  # no provider inherits the default provider's stored key — same primitive
        return (
            "base_url override requires an explicit provider. Set provider to a "
            "configured custom provider to use a custom endpoint.")
    try:
        from hermes_cli.runtime_provider import (
            has_named_custom_provider,
            resolve_requested_provider,
            _get_named_custom_provider)
        from hermes_cli.auth import PROVIDER_REGISTRY
    except Exception:
        return f"Unable to validate base_url override for provider {prov!r}; refused."

    if prov.lower() == "custom":
        return _custom_stored_key_error(bu)
    if has_named_custom_provider(prov):
        # A NAMED custom provider's STORED key is still sent to an override base_url.
        try:
            cp = _get_named_custom_provider(prov)
        except Exception:
            cp = None
        cfg_url = str((cp or {}).get("base_url") or "")
        if cfg_url and _same_origin(bu, cfg_url):
            return None
        return _base_url_refused(
            bu, prov, "A named custom provider's stored credential may only be sent to its own "
            f"configured endpoint ({cfg_url or 'unknown'}): same scheme, host and port.")
    try:
        resolved = resolve_requested_provider(prov)
    except Exception:
        resolved = prov
    pconfig = PROVIDER_REGISTRY.get(resolved) if isinstance(resolved, str) else None
    known_url = str(getattr(pconfig, "inference_base_url", "") or "") if pconfig else ""
    if known_url and _same_origin(bu, known_url):
        return None
    # Fail closed: named providers with stored credentials AND unknown names we cannot origin-match.
    return _base_url_refused(
        bu, prov, "A named provider's stored credential may only be sent to its own endpoint "
        '(same scheme, host and port); use a configured custom provider (provider="custom") '
        "for a custom base_url.")


def _validate_cron_script_path(script: Optional[str]) -> Optional[str]:
    """Scripts must be relative paths within HERMES_HOME/scripts/ (absolute / ~ / drive-letter
    rejected — prompt-injection guard). Error string if blocked, else None; empty = clear."""
    if not script or not script.strip():
        return None

    from hermes_constants import get_hermes_home
    raw = script.strip()
    scripts_dir = get_hermes_home() / "scripts"
    if raw.startswith(("/", "~")) or (len(raw) >= 2 and raw[1] == ":"):
        return (
            f"Script path must be relative to {scripts_dir}/. "
            f"Got absolute or home-relative path: {raw!r}. "
            f"Place scripts in {scripts_dir}/ and use just the filename.")

    from tools.path_security import validate_within_dir
    scripts_dir.mkdir(parents=True, exist_ok=True)
    resolved_script = scripts_dir / raw
    if validate_within_dir(resolved_script, scripts_dir):
        return f"Script path escapes the scripts directory via traversal: {raw!r}"
    if not resolved_script.is_file():
        return (
            f"Script file not found: {resolved_script}. "
            f"Create it in {scripts_dir}/ first.")
    return None


def _apply_continuity(
    context_from: Optional[str | list[str]],
    continuity: bool) -> Optional[list[str]]:
    """continuity=True ensures "self" is in context_from; False removes it; others untouched."""
    refs = _clean_str_list(context_from)
    has_self = any(r.lower() == "self" for r in refs)
    if continuity and not has_self:
        refs.append("self")
    elif not continuity and has_self:
        refs = [r for r in refs if r.lower() != "self"]
    return refs or None


def _validate_context_from_refs(refs: list[Any]) -> Optional[str]:
    """Error string if any non-"self" ref names a missing job ("self" resolves to the job's
    own id at run time, so it can't be checked — the job doesn't exist yet at create)."""
    from cron.jobs import get_job as _get_job
    for ref_id in refs:
        if isinstance(ref_id, str) and ref_id.strip().lower() == "self":
            continue
        if not _get_job(ref_id):
            return (
                f"context_from job '{ref_id}' not found. "
                "Use cronjob(action='list') to see available jobs.")
    return None


# Optional fields echoed by _format_job only when truthy (order = JSON key order).
_FORMAT_JOB_OPTIONAL_KEYS = (
    "script", "reasoning_effort", "monitor_script", "monitor_url",
    "monitor_state", "no_agent", "enabled_toolsets", "workdir", "interpreter")


def _format_job(job: dict[str, Any]) -> dict[str, Any]:
    from agent.redact import redact_sensitive_text

    prompt = str(job.get("prompt") or "")
    skills = _canonical_skills(job.get("skill"), job.get("skills"))
    job_id = str(job.get("id") or "unknown")
    name = str(job.get("name") or prompt[:50] or (skills[0] if skills else "") or job_id or "cron job")
    result = {
        "job_id": job_id,
        "name": name,
        "skill": skills[0] if skills else None,
        "skills": skills,
        "prompt_preview": prompt[:100] + "..." if len(prompt) > 100 else prompt,
        "model": job.get("model"),
        "provider": job.get("provider"),
        # Locked to its own model; unpinned jobs follow cron.model, then the main agent model.
        "pinned": bool(str(job.get("model") or "").strip()),
        "base_url": job.get("base_url"),
        "schedule": job.get("schedule_display") or "?",
        "repeat": _repeat_display(job),
        "deliver": job.get("deliver", "local"),
        "next_run_at": job.get("next_run_at"),
        "last_run_at": job.get("last_run_at"),
        "last_status": job.get("last_status"),
        "last_delivery_error": job.get("last_delivery_error"),
        "last_delivery_unverified": job.get("last_delivery_unverified"),
        "last_fire_error": job.get("last_fire_error"),
        "last_error": redact_sensitive_text(
            job["last_error"], force=True, redact_url_credentials=True,
        ) if job.get("last_error") else job.get("last_error"),
        "enabled": job.get("enabled", True),
        # Derive from enabled so half-paused records never render as paused.
        "state": effective_job_state(job),
        "paused_at": job.get("paused_at"),
        "paused_reason": job.get("paused_reason"),
    }
    for key in _FORMAT_JOB_OPTIONAL_KEYS:
        if job.get(key):
            result[key] = True if key == "no_agent" else job[key]
    stored_refs = job.get("context_from") or []
    if isinstance(stored_refs, str):
        stored_refs = [stored_refs]
    is_self = lambda r: str(r).strip().lower() == "self" or r == job.get("id")
    if any(is_self(r) for r in stored_refs):
        result["continuity"] = True
    external_refs = [r for r in stored_refs if not is_self(r)]
    if external_refs:
        result["context_from"] = external_refs
    if isinstance(job.get("attach_to_session"), bool):
        result["attach_to_session"] = job["attach_to_session"]
    return result




def _validate_local_delivery(deliver: Optional[str]) -> Optional[str]:
    from cron.scheduler_delivery import local_delivery_error
    return local_delivery_error({"deliver": deliver})
