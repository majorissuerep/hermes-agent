"""Host-attached TUI launches: ``hermes --tui`` in deck mode, bare ``hermes deck``, ``hermes deck attach``.

In deck mode the Ink client dials the machine session host instead of spawning a private
``tui_gateway`` child, so the session lives in the host: quitting or killing the terminal only
detaches it. The client learns its profile and working directory from the env below (the host's own
env and cwd are the machine root's) and registers each session it opens in the deck.
"""

from __future__ import annotations

import contextlib
import os
import sys
from typing import Optional


def deck_host_enabled() -> bool:
    """``sessions.host`` (default on)."""
    with contextlib.suppress(Exception):
        from hermes_cli.config import load_config

        return bool(((load_config().get("sessions") or {}).get("host", True)))
    return True


def configure_deck_host(env: dict, *, profile: Optional[str] = None, cwd: Optional[str] = None,
                        required: bool = False) -> bool:
    """Point the TUI at the session host. False → the caller keeps the classic private backend."""
    if env.get("HERMES_TUI_GATEWAY_URL") or not (required or deck_host_enabled()):
        return False
    from hermes_cli.session_deck import current_profile
    from hermes_cli.session_host import HostUnavailable, ensure_host

    try:
        url = ensure_host()
    except (HostUnavailable, OSError) as exc:
        print(f"⚠ Session host unavailable ({exc}).", file=sys.stderr)
        if not required:
            print("  This session will end with its terminal.", file=sys.stderr)
        return False
    env.update(HERMES_TUI_GATEWAY_URL=url, HERMES_TUI_DECK="1",
               HERMES_TUI_PROFILE=profile or current_profile(), HERMES_TUI_CWD=cwd or os.getcwd())
    return True


def print_deck_exit_summary(active_session_file: Optional[str], profile: Optional[str]) -> None:
    """After the client exits: the session is still open — say how to get back to it."""
    from hermes_cli.main_tui_launch import _read_tui_active_session_file
    from hermes_cli.session_deck import DeckRef, current_profile, open_db

    session_id = _read_tui_active_session_file(active_session_file)
    if not session_id:
        return
    profile = profile or current_profile()
    with contextlib.suppress(Exception), open_db(profile) as db:
        row = db.deck_row_for_session(session_id)
        if row is None or row["closed_at"] is not None:
            return
        ref = DeckRef(profile, row["handle"])
        title = f' "{row["title"]}"' if row["title"] else ""
        print(f"\n◌ {ref}{title} stays open in the session host.")
        print(f"  Back to it:  hermes deck attach {ref}     All sessions:  hermes deck")


def launch_deck_attach(row: dict) -> int:
    from hermes_cli.main_tui_launch import _launch_tui

    return _launch_tui(resume_session_id=row["tip_session_id"], deck_profile=row["profile"],
                       deck_cwd=row["cwd"] or None)


def launch_deck_tui() -> int:
    from hermes_cli.main_tui_launch import _launch_tui

    return _launch_tui(deck_home=True)
