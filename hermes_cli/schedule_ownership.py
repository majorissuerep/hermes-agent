"""Destination checks for persisted in-session schedules, not remote delivery."""
from __future__ import annotations

from typing import Any


def can_run_locally(session_id: str, route: dict, db: Any) -> bool:
    """Explicit local controls claim ownership; old unrouted native sessions keep working.

    Historical remote or unknown destinations remain passive, without rewriting their state.
    An immutable remote conversation source is not a permanent ban: explicit set/resume
    records a local destination for that schedule only.
    """
    if route:
        return route == {"kind": "local"}
    if db is None:
        return False
    session = db.get_session(session_id)
    return session is not None and session.get("source") in {"cli", "tui"}
