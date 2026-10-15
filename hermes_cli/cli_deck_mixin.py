"""Classic REPL deck membership, with all state.db writes owned by the gateway."""

from __future__ import annotations

import contextlib
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Idle-tick throttle: the REPL idles in 0.1s slices; the deck checks (a state.db read + a mailbox stat)
# need not run that often.
_DECK_POLL_S = 1.0


class _DeckMessage(str):
    """Queued deck input; the distinct object lets the REPL recognise the turn that answers it."""


class CLIDeckMixin:
    _deck_handle = None
    _deck_inflight = None  # (delivery id, queued input object) while a deck message is queued/running
    _deck_running = None  # delivery id whose turn is running now
    _deck_polled_at = 0.0
    _last_chat_response = None

    def _deck_home(self) -> Path:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home()).resolve()

    def _deck_register(self) -> None:
        """Open (or re-open) this conversation's deck row. Best-effort: the REPL works without a deck."""
        try:
            self._deck_handle = self._session_db.deck_open(str(self.session_id), surface="cli", cwd=os.getcwd())
        except Exception:
            logger.debug("deck registration unavailable", exc_info=True)

    def _deck_ref(self) -> str:
        from hermes_cli.session_deck import DeckRef, current_profile

        return str(DeckRef(current_profile(), self._deck_handle)) if self._deck_handle else ""

    def _deck_idle_tick(self) -> None:
        now = time.monotonic()
        if self._deck_handle is None or now - self._deck_polled_at < _DECK_POLL_S:
            return
        self._deck_polled_at = now
        self._deck_follow_session()
        if self._deck_closed_elsewhere():
            return
        self._deck_claim_delivery()

    def _deck_follow_session(self) -> None:
        """``/new``, ``/resume`` and compression change ``session_id``: move the lease (so liveness and
        mailbox pins name the conversation actually running) and open the new conversation's row."""
        lease = getattr(self, "_active_session_lease", None)
        if lease is None or lease.session_id == str(self.session_id):
            return
        from hermes_cli.active_sessions import transfer_active_session

        if transfer_active_session(lease, session_id=str(self.session_id), metadata=self._deck_lease_metadata()):
            self._deck_register()

    def _deck_lease_metadata(self) -> dict:
        return {"live_session_id": str(self.session_id), "bot_live_delivery_consumer": True}

    def _deck_closed_elsewhere(self) -> bool:
        try:
            row = self._session_db.deck_row(self._deck_handle)
        except Exception:
            return False
        if row is None or row["closed_at"] is None:
            return False
        from cli import _cprint

        by = f" by {row['closed_by']}" if row.get("closed_by") else ""
        _cprint(f"\n■ {self._deck_ref()} was closed{by}. Exiting.")
        self._deck_handle = None
        self._should_exit = True
        with contextlib.suppress(Exception):
            if self._app.is_running:
                self._app.exit()
        return True

    def _deck_claim_delivery(self) -> None:
        """Take one deck message pinned to this REPL's lease and queue it as the next user turn."""
        if self._deck_inflight is not None or self._agent_running or not self._pending_input.empty():
            return
        lease = getattr(self, "_active_session_lease", None)
        if lease is None or getattr(lease, "released", False):
            return
        from tools.bot_live_delivery import claim_pending_deck_delivery, has_mailbox

        home = self._deck_home()
        if not has_mailbox(home):
            return
        owner = {"profile_home": str(home), "session_id": str(self.session_id), "lease_id": lease.lease_id,
                 "live_session_id": str(self.session_id)}
        try:
            claimed = claim_pending_deck_delivery(home, owner)
        except Exception:
            logger.debug("deck mailbox claim failed", exc_info=True)
            return
        if claimed is None:
            return
        # A real user turn (its text already names the sender), not a timeline notification.
        message = _DeckMessage(claimed["message"])
        self._deck_inflight = (str(claimed["id"]), message)
        self._pending_input.put(message)

    def _deck_note_input(self, user_input) -> None:
        """Called as each input starts: remember whether THIS turn answers the claimed deck message."""
        inflight = self._deck_inflight
        self._deck_running = inflight[0] if inflight is not None and user_input is inflight[1] else None

    def _deck_after_turn(self) -> None:
        """Write the claimed message's receipt — the reply goes back to a sender that waits for it."""
        delivery_id, self._deck_running = self._deck_running, None
        if delivery_id is None:
            return
        self._deck_inflight = None
        from tools.bot_live_delivery import complete_delivery

        reply = self._last_chat_response
        status = "settled" if reply is not None and not getattr(self, "_last_turn_interrupted", False) else (
            "cancelled" if getattr(self, "_last_turn_interrupted", False) else "failed")
        try:
            complete_delivery(self._deck_home(), delivery_id, status=status, reply=reply or "",
                              error="" if status == "settled" else "the session did not answer")
        except Exception:
            logger.warning("deck delivery receipt failed for %s", delivery_id, exc_info=True)

    def _handle_close_command(self, cmd_original: str):
        """``/close``: end this session for good — it leaves the deck. (``/quit`` leaves it open.)"""
        from cli import _cprint
        from hermes_cli.session_deck import DECK_CLOSE_REASON, DeckRef, current_profile

        if self._deck_handle is not None:
            ref = DeckRef(current_profile(), self._deck_handle)
            try:
                self._session_db.deck_close(self._deck_handle, reason=DECK_CLOSE_REASON, closed_by="/close")
            except Exception as exc:
                _cprint(f"Could not close {ref}: {exc}")
                return True
            self._deck_handle = None
            _cprint(f"■ {ref} closed.")
        return False
