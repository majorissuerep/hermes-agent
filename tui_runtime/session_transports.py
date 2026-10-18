"""The private stdio peer associated with a local terminal session."""
from __future__ import annotations

from tui_runtime.method_ctx import bind_module


def _session_transport_contains(session: dict | None, transport) -> bool:
    return bool(session and transport is not None and session.get("transport") is transport
                and not _transport_is_dead(transport))


def _attach_session_transport(session: dict | None, transport) -> bool:
    if not session or transport is None or _transport_is_dead(transport):
        return False
    session["transport"] = transport
    return True


def register(server) -> None:
    bind_module(globals(), server, skip=("_",))
