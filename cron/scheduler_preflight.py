"""Cron pre-run preflight: transient provider-resolution error classification, provider-key /
delivery-target / skills checks, and the shared-route adapter view used by satellite profiles.

Split out of ``cron.scheduler``. Import names from this module directly (``cron.scheduler`` only
imports the few it calls itself). Origin-resident helpers and sibling split modules are reached
late-bound (``_sched`` / module refs at the bottom) so monkeypatching the defining module works.
"""

from __future__ import annotations

import errno
import json
import logging
import os
from typing import Optional

from cron.env_settings import cron_env_setting

# Log-record parity with the origin module.
logger = logging.getLogger("cron.scheduler")

# Error-string prefixes from ``run_job``; ``run_one_job`` keys off them for last_status and the
# alert-once dedup. ``:silent`` = already alerted on a previous tick — do not deliver again.
BLOCKED_CONFIG_MARKER = "[blocked_config]"
BLOCKED_CONFIG_SILENT_MARKER = "[blocked_config:silent]"

_TRANSIENT_NET_EXC_NAMES = frozenset({
    "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout", "NetworkError",
    "TimeoutException", "ClientConnectorError", "ClientConnectorDNSError", "ServerTimeoutError",
    "ClientOSError"})
_DNS_FAILURE_NEEDLES = ("nodename nor servname", "name or service not known")
_TRANSIENT_OSERROR_NEEDLES = _DNS_FAILURE_NEEDLES + (
    "temporary failure in name resolution", "network is unreachable")
_TRANSIENT_HTTP_NEEDLES = _TRANSIENT_OSERROR_NEEDLES + (
    "failed to resolve", "connection refused", "timed out", "timeout")
_TRANSIENT_ERRNOS = frozenset({
    errno.ECONNREFUSED, errno.ECONNRESET, errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ENETDOWN,
    errno.ETIMEDOUT, errno.EAGAIN})


def _is_transient_provider_resolve_error(exc: BaseException) -> bool:
    """True when primary provider resolution failed for a transient network reason (DNS blip,
    ConnectError...). Must be eligible for ``fallback_providers`` like AuthError, else a healthy
    fallback rung is never tried and the job dies before the first model call."""
    import socket

    # gaierror carries EAI_* codes, plain OSError carries errno — never mix the namespaces (raw
    # literals like {8, 7, 11} are macOS-only and wrong on Linux).
    eai_transient = {
        getattr(socket, n) for n in ("EAI_NONAME", "EAI_AGAIN", "EAI_FAIL", "EAI_NODATA")
        if hasattr(socket, n)
    }
    # Walk the cause chain; the scheduler wraps raw transport errors.
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        module = type(cur).__module__ or ""
        msg = str(cur).lower()
        if type(cur).__name__ in _TRANSIENT_NET_EXC_NAMES:
            return True
        if any(m in module for m in ("httpx", "httpcore", "aiohttp")) and any(
            needle in msg for needle in _TRANSIENT_HTTP_NEEDLES):
            return True
        if isinstance(cur, OSError):
            if isinstance(cur, socket.gaierror):
                if cur.errno in eai_transient:
                    return True
            elif getattr(cur, "errno", None) in _TRANSIENT_ERRNOS:
                return True
            if any(needle in msg for needle in _TRANSIENT_OSERROR_NEEDLES):
                return True
        # Bare exceptions that carry the raw DNS text (format_runtime_provider_error).
        if any(needle in msg for needle in _DNS_FAILURE_NEEDLES):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _cron_preflight_enabled(cfg: dict) -> bool:
    """Preflight is ON unless ``cron.preflight`` is literally ``false``."""
    cron_cfg = (cfg or {}).get("cron")
    return not isinstance(cron_cfg, dict) or cron_cfg.get("preflight", True) is not False


def _preflight_check_provider_key(job: dict, cfg: dict) -> Optional[str]:
    """READ-ONLY probe: would provider resolution fail for lack of a key? Mirrors run_job's
    requested-provider computation. Skipped when the job has a fallback chain — auth-fallback may
    legitimately rescue a missing primary key. A pinned job has none (``_job_fallback_chain``), so
    its missing key blocks even when the global chain is configured."""
    try:
        if _sched._job_fallback_chain(job, cfg):
            return None
    except Exception:
        return None  # fail-open: never block on a preflight-internal error

    _cron_cfg = cfg.get("cron") if isinstance(cfg.get("cron"), dict) else {}
    requested = (
        job.get("provider") or str((_cron_cfg or {}).get("model_provider") or "").strip() or None)
    model = job.get("model") or cron_env_setting("HERMES_MODEL") or ""

    from hermes_cli.auth import AuthError, is_rate_limited_auth_error
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider
        kwargs = {"requested": requested, "target_model": model}
        if job.get("base_url"):
            kwargs["explicit_base_url"] = job.get("base_url")
        resolve_runtime_provider(**kwargs)
    except AuthError as exc:
        if is_rate_limited_auth_error(exc):
            # Quota/rate-limit is not a missing credential: let the real path report it and hold
            # the job through the provider's window (cron/quota_hold.py, #89376).
            return None
        return (
            f"provider credential missing: {exc} {_credential_store_scope_label()}. "
            "Set the provider API key in .env (or `hermes setup`) for that home, or pin a "
            "working provider via `hermes cron edit "
            f"{job.get('id')} --provider <p>`."
        )
    except Exception:
        return None  # non-auth errors are not a missing-credential verdict; real path reports them
    return None


def _credential_store_scope_label() -> str:
    """``[profile '<name>', HERMES_HOME <path>]`` for the home this preflight read credentials from.

    The verdict must name the store it judged: a scheduler process whose home differs from the
    shell where "the same credential works" (Docker HOME vs HERMES_HOME, a multiplexed satellite
    profile, a gateway launched without the shell's env) otherwise reports a bare "No credentials
    stored" that cannot be told apart from a real login gap (#116213).
    """
    from hermes_cli.profiles import get_active_profile_name
    from hermes_constants import get_hermes_home
    return f"[profile '{get_active_profile_name() or 'default'}', HERMES_HOME {get_hermes_home()}]"


def _preflight_check_delivery(job: dict) -> Optional[str]:
    """Removed transports are an unconditional configuration error, never a probe."""
    return _delivery.local_delivery_error(job)


# ``skill_view`` payload keys naming missing prerequisites -> label for the preflight verdict.
_SKILL_MISSING_FIELDS = (
    ("missing_required_environment_variables", "env ${}"),
    ("missing_required_commands", "command '{}'"),
    ("missing_credential_files", "credential file {}"))


def _preflight_check_skills(job: dict) -> Optional[str]:
    """Block only on an affirmative ``setup_needed`` verdict from ``skill_view``; skills that fail
    to load fall through to ``_build_job_prompt``'s skipped-skill handling (fail-open)."""
    from cron.scheduler_prompt import _job_skill_names
    skill_names = _job_skill_names(job)
    if not skill_names:
        return None
    from tools.skills_tool import skill_view
    for skill_name in skill_names:
        try:
            payload = json.loads(skill_view(skill_name))
        except Exception:
            continue  # unreadable/missing skill → existing skip handling
        if not isinstance(payload, dict) or not payload.get("success"):
            continue
        if payload.get("setup_needed") or payload.get("readiness_status") == "setup_needed":
            missing = [
                fmt.format(name)
                for key, fmt in _SKILL_MISSING_FIELDS
                for name in payload.get(key) or []
            ]
            detail = ", ".join(missing) or "required setup incomplete"
            return (
                f"attached skill '{skill_name}' is not ready: missing "
                f"{detail}. Provide the missing prerequisites or detach the "
                "skill from this job."
            )
    return None


# (job id, server name) pairs already warned about as reconnecting; see _empty_requested_mcp_toolsets.
_RECONNECTING_WARNED: set = set()


def _empty_requested_mcp_toolsets(job: dict, cfg: dict) -> Optional[str]:
    """Reason when an MCP server the job's own ``enabled_toolsets`` names resolves to zero tools.

    Runs AFTER cron MCP discovery. The server's toolset alias is process-global while its tools
    are registered per profile overlay, so under a multiplexer a job can name a server that is
    connected for another profile and build a tool-less agent that ``quiet_mode`` never reports.
    Only servers the job explicitly asked for count; the implicit enabled-server merge does not.
    """
    requested = [str(name) for name in (job.get("enabled_toolsets") or [])]
    if not requested:
        return None
    from hermes_cli.tools_config import enabled_mcp_server_names
    from toolsets import resolve_toolset
    from tools.mcp_tool_discovery import mcp_server_reconnecting
    missing = [name for name in requested
               if name in enabled_mcp_server_names(cfg) and not resolve_toolset(name)]
    # A server that worked in this process and is parked/self-probing after a network blip
    # (router reboot, DNS failure) is recovering, not misconfigured: the job runs with the tools
    # that did resolve rather than losing a whole tick to a minute of downtime (#112871). Only a
    # server that never connected for this profile is judged below.
    reconnecting = sorted(name for name in missing if mcp_server_reconnecting(name))
    job_id = str(job.get("id", "?"))
    # One WARNING per job+server per outage (like the one-shot blocked_config alert), not one per
    # tick; the entry drops once the server is back so the next outage warns again.
    _RECONNECTING_WARNED.difference_update(
        key for key in list(_RECONNECTING_WARNED) if key[0] == job_id and key[1] not in reconnecting)
    unwarned = [name for name in reconnecting if (job_id, name) not in _RECONNECTING_WARNED]
    if unwarned:
        _RECONNECTING_WARNED.update((job_id, name) for name in unwarned)
        logger.warning(
            "Job '%s': MCP server(s) %s named in enabled_toolsets are reconnecting — running "
            "without their tools until they recover (a server parked on a permanent error blocks "
            "the job instead)", job_id, ", ".join(unwarned))
    if reconnecting:
        missing = [name for name in missing if name not in reconnecting]
    if not missing:
        return None
    # The reason is what the operator reads in the gateway log and the alert. It must say the
    # block is not sticky: a server whose first connection failed on a network blip is parked and
    # self-probed by the MCP layer, and this check re-runs on every dispatch, so the job resumes
    # on its own — two operators misread the old text as a config error to repair by hand (#112871).
    return (
        f"MCP server(s) {', '.join(sorted(missing))} named in this job's enabled_toolsets "
        "resolved to zero tools for this profile (never connected for this profile, or connected "
        "for another profile only). If the server is only temporarily unreachable this clears by itself — "
        "the check re-runs on every dispatch and the job resumes once the server reconnects. "
        "If the name is wrong or belongs to another profile, fix the server or remove it from "
        "the job's toolsets.")


def _preflight_job_config(job: dict, cfg: dict) -> Optional[str]:
    """Pre-dispatch validation: return a reason (missing key, unconfigured delivery, unready skill)
    so the caller refuses BEFORE building agent machinery or burning an LLM call. Every check fails
    open — preflight blocks only on an affirmative misconfiguration verdict.

    Same fail-before-spend spirit as the fail-loud-on-hidden-tools direction in #27948; alert dedup
    follows the alert-once pattern from the dead-pin auto-pause (#73506).
    """
    delivery_error = _preflight_check_delivery(job)
    if delivery_error:
        return delivery_error
    for name, check in (
        ("provider_key", lambda: _preflight_check_provider_key(job, cfg)),
        ("skills", lambda: _preflight_check_skills(job))):
        try:
            reason = check()
        except Exception:
            logger.debug("preflight check %s raised — failing open", name, exc_info=True)
            continue
        if reason:
            return reason
    return None


# Late-bound origin namespace (see module docstring). Imported LAST so this module is fully
# populated before ``scheduler`` re-exports from it.
from cron import scheduler as _sched
from cron import scheduler_delivery as _delivery
