"""Classic REPL in the session deck (``hermes_cli/cli_deck_mixin.py``), against the real lease registry, mailbox
and state.db: a deck message reaches the REPL as its next user turn with the reply as the receipt, the lease
follows ``/new``, and a close issued elsewhere ends the REPL."""

import queue
from unittest.mock import MagicMock

import pytest

from hermes_cli.active_sessions import try_acquire_active_session
from hermes_cli.cli_deck_mixin import CLIDeckMixin
from hermes_cli.session_deck import DeckRef, close_row, live_owner, open_db
from tools import bot_live_delivery as mailbox


class _Repl(CLIDeckMixin):
    def __init__(self, session_id):
        self.session_id = session_id
        self._pending_input = queue.Queue()
        self._agent_running = False
        self._should_exit = False
        self._last_turn_interrupted = False
        self._app = MagicMock(is_running=False)
        lease, refusal = try_acquire_active_session(
            session_id=session_id, surface="cli", config={}, metadata=self._deck_lease_metadata())
        assert refusal is None
        self._active_session_lease = lease
        self._deck_register()

    def tick(self):
        self._deck_polled_at = 0.0
        self._deck_idle_tick()


@pytest.fixture
def repl():
    made = []

    def make(session_id):
        made.append(_Repl(session_id))
        return made[-1]

    yield make
    for r in made:
        r._active_session_lease.release()


def test_deck_message_runs_as_next_turn_and_its_reply_is_the_receipt(repl):
    r = repl("cli-a")
    home = r._deck_home()
    pin = mailbox.live_owner_pin(live_owner("default", "cli-a"))
    assert pin is not None, "an interactive REPL must advertise that it consumes deck messages"
    ticket = mailbox.deliver_to_live_owner(home, pin, "[Session deck — message from session #2]\nstatus?")

    r.tick()
    queued = r._pending_input.get_nowait()
    assert queued == ticket["message"]
    assert mailbox.read_delivery_result(home, ticket["id"])["status"] == "claimed"

    r._deck_note_input(queued)  # the REPL starts this input as a turn …
    r._last_chat_response = "all green"
    r._deck_after_turn()
    receipt = mailbox.read_delivery_result(home, ticket["id"])
    assert (receipt["status"], receipt["reply"]) == ("settled", "all green")


def test_lease_and_deck_row_follow_a_new_conversation(repl):
    r = repl("cli-old")
    old_handle = r._deck_handle
    r.session_id = "cli-new"  # what /new does
    r.tick()
    assert r._active_session_lease.session_id == "cli-new"
    assert r._deck_handle != old_handle
    with open_db("default") as db:
        assert db.deck_row(old_handle)["closed_at"] is None  # the old conversation stays open


def test_close_from_elsewhere_ends_the_repl(repl):
    r = repl("cli-a")
    assert close_row(DeckRef("default", r._deck_handle), closed_by="session #2")
    r.tick()
    assert r._should_exit is True
    assert r._pending_input.empty()
