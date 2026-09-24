"""Session deck — the machine-wide view of OPEN sessions across every profile.

A session is open from creation until an explicit close (``/close``, ``hermes deck close``, or
another session closing it); terminal death, host restarts and idle eviction only move it between
runtime states. The registry lives in each profile's ``state.db`` (``hermes_state_deck.py``, SQLCipher
at rest); liveness comes from the active-session lease registry, never from the snapshot alone, so a
crashed owner reads as ``dormant`` instead of a ghost ``running``.

Addressing: ``#7`` is handle 7 in the caller's profile, ``work#7`` handle 7 in profile ``work``.
Everything here is host-independent: the session host (``tui_gateway/methods_deck.py``) and the
classic CLI both build on it.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

DECK_CLOSE_REASON = "deck_close"
_REF_RE = re.compile(r"^\s*(?:(?P<profile>[a-z0-9][a-z0-9_-]*)\s*[#:])?\s*#?(?P<handle>\d+)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class DeckRef:
    profile: str
    handle: int

    def __str__(self) -> str:
        return f"#{self.handle}" if self.profile == "default" else f"{self.profile}#{self.handle}"


def parse_ref(text: str, *, default_profile: str = "default") -> DeckRef:
    """``#7`` / ``7`` → the caller's profile; ``work#7`` / ``work:7`` → profile ``work``."""
    match = _REF_RE.match(str(text or ""))
    if match is None:
        raise ValueError(f"not a deck reference: {text!r} (expected #N or <profile>#N)")
    from hermes_cli.profiles import normalize_profile_name

    profile = normalize_profile_name(match.group("profile") or default_profile)
    return DeckRef(profile=profile, handle=int(match.group("handle")))


def profile_home(profile: str) -> Path:
    from hermes_cli.profiles import get_profile_dir

    return Path(get_profile_dir(profile)).resolve()


def current_profile() -> str:
    """The profile of the running process's HERMES_HOME (``default`` for the root home)."""
    with contextlib.suppress(Exception):
        from hermes_cli.profiles import get_active_profile_name

        return get_active_profile_name() or "default"
    return "default"


def deck_profiles() -> List[str]:
    """Every profile that has a state.db to hold deck rows."""
    from hermes_cli.profiles import list_profile_names

    return [name for name in list_profile_names() if (profile_home(name) / "state.db").is_file()]


@contextlib.contextmanager
def open_db(profile: str) -> Iterator[Any]:
    from hermes_state import SessionDB

    db = SessionDB(db_path=profile_home(profile) / "state.db")
    try:
        yield db
    finally:
        db.close()


def live_owner(profile: str, tip_session_id: str) -> Optional[Dict[str, Any]]:
    """The active-session lease of whatever process runs this conversation now, or None (dormant)."""
    from tools.bot_live_delivery import find_session_owner

    return find_session_owner(profile_home(profile), tip_session_id)


def _project(profile: str, row: Dict[str, Any]) -> Dict[str, Any]:
    owner = live_owner(profile, row["tip_session_id"])
    state = row["state"] if owner is not None else "dormant"
    if owner is not None and state == "dormant":
        state = "idle"
    return {**row, "profile": profile, "ref": str(DeckRef(profile, row["handle"])), "state": state,
            "owner_surface": (owner or {}).get("surface") or "", "owner_pid": (owner or {}).get("pid")}


def list_deck(profiles: Optional[List[str]] = None, *, include_closed: bool = False) -> List[Dict[str, Any]]:
    """Open sessions across ``profiles`` (default: all), most recently active first."""
    rows: List[Dict[str, Any]] = []
    for profile in profiles if profiles is not None else deck_profiles():
        with open_db(profile) as db:
            rows.extend(_project(profile, row) for row in db.deck_rows(include_closed=include_closed))
    return sorted(rows, key=lambda r: r["last_active"], reverse=True)


def resolve(ref: DeckRef) -> Dict[str, Any]:
    """The projected open row for ``ref``; LookupError when unknown or already closed."""
    try:
        home = profile_home(ref.profile)
    except (ValueError, FileNotFoundError) as exc:
        raise LookupError(str(exc)) from exc
    if not (home / "state.db").is_file():
        raise LookupError(f"profile {ref.profile!r} has no sessions")
    with open_db(ref.profile) as db:
        row = db.deck_row(ref.handle)
    if row is None:
        raise LookupError(f"no session {ref}")
    if row["closed_at"] is not None:
        raise LookupError(f"session {ref} is closed ({row['close_reason']})")
    return _project(ref.profile, row)


def close_row(ref: DeckRef, *, closed_by: str) -> bool:
    """Close the deck row and end its stored session. The owning process reacts to the row: the
    host tears its runtime down in the same call (``methods_deck``), a classic CLI watches its row."""
    with open_db(ref.profile) as db:
        row = db.deck_row(ref.handle)
        if row is None or not db.deck_close(ref.handle, reason=DECK_CLOSE_REASON, closed_by=closed_by):
            return False
        if row["started"]:
            db.end_session(row["tip_session_id"], DECK_CLOSE_REASON)
    return True


def tail(profile: str, tip_session_id: str, limit: int = 12) -> List[Dict[str, str]]:
    """Last ``limit`` user/assistant messages as plain text (tool traffic elided)."""
    with open_db(profile) as db:
        messages = db.get_messages(tip_session_id)
    out: List[Dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role not in ("user", "assistant") or not content:
            continue
        text = content if isinstance(content, str) else " ".join(
            part.get("text", "") for part in content if isinstance(part, dict))
        if text.strip():
            out.append({"role": role, "text": text.strip()})
    return out[-limit:]


def sender_label(sender: Dict[str, Any]) -> str:
    """Human label for a message's origin: ``session #3 "title"`` or the free-form actor name."""
    if sender.get("ref"):
        title = sender.get("title") or ""
        return f"session {sender['ref']}" + (f' "{title}"' if title else "")
    return str(sender.get("name") or "the user")


def frame_message(message: str, sender: Dict[str, Any]) -> str:
    """The text the TARGET model sees: one provenance line, then the message verbatim. A visible header
    (not just turn_author, which only reaches memory providers) so the recipient can tell a relayed
    request from its own user and reply through the deck if it needs to."""
    return f"[Session deck — message from {sender_label(sender)}]\n{message}"


def sender_author(sender: Dict[str, Any]) -> Dict[str, Any]:
    ident = f"deck:{sender['ref']}" if sender.get("ref") else "deck:user"
    return {"id": ident, "name": sender_label(sender), "is_bot": bool(sender.get("ref"))}
