"""Shared-metrics RPC compatibility with this fork's disabled telemetry policy.

Bodies are rebound onto server.py's globals by method_ctx.bind_module.
"""

import logging

from .method_ctx import HandlerRegistry, bind_module

logger = logging.getLogger(__name__)
_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _shared_metrics_consent(cfg) -> dict:
    """Keep clients from offering a feature this fork permanently disables."""
    return {"enabled": False, "send": False, "decided": True}


def _tui_model_friction(signal, session, turns=1):
    from hermes_cli.observability.shared_metrics_model import record_model_friction
    record_model_friction(
        signal, session_id=session.get("session_key"), agent=session.get("agent"),
        hermes_home=session.get("profile_home"), turns=turns)


@method("shared_metrics.status")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """``{enabled, send, decided}`` for the focused profile. Reports the fork policy even for
    profiles retaining an upstream opt-in."""
    try:
        return _ok(rid, _shared_metrics_consent(_load_cfg()))
    except Exception as e:
        return _err(rid, 5095, str(e))


@method("shared_metrics.set")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """Normalize legacy clients' requests to the fork's disabled telemetry policy."""
    try:
        cfg = _load_cfg_raw()
        telemetry = cfg.get("telemetry")
        if not isinstance(telemetry, dict):
            telemetry = cfg["telemetry"] = {}
        section = telemetry.get("shared_metrics")
        if not isinstance(section, dict):
            section = telemetry["shared_metrics"] = {}
        section["enabled"], section["send"] = False, False
        _save_cfg(cfg)
    except Exception as e:
        logger.warning("Could not persist the disabled telemetry preference", exc_info=True)
        return _err(rid, 5096, str(e))
    return _ok(rid, _shared_metrics_consent(cfg))


@method("shared_metrics.slash_command")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """The Desktop and Ink TUI dispatchers call this once per user-typed slash command, locally
    handled ones included; the gateway never counts slash.exec / command.dispatch itself, so
    each command lands exactly once. Always ``{ok: true}`` (the events API never raises)."""
    from hermes_cli.observability.shared_metrics_events import record_slash_command

    record_slash_command(command=str(params.get("command") or ""), surface=_resolve_session_platform())
    return _ok(rid, {"ok": True})


@method("shared_metrics.startup_latency")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """Count the private renderer's ready notification once per terminal launch."""
    if params.get("surface") not in (None, "tui"):
        return _err(rid, 4000, "surface must be tui")
    from hermes_cli.observability.shared_metrics_startup import record_rpc_startup_latency

    record_rpc_startup_latency(
        client_surface=params.get("surface") or _resolve_session_platform(), elapsed_ms=params.get("elapsed_ms"),
        launch_id=params.get("launch_id"),
    )
    return _ok(rid, {"ok": True})







def register(server) -> None:
    bind_module(globals(), server, skip=("_",))
