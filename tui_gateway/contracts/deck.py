"""Session deck (``deck.*``): the machine-wide registry of open sessions and the cross-session verbs.

Handlers: ``tui_gateway/methods_deck.py``; row shapes: ``hermes_cli/session_deck.py`` over
``hermes_state_deck.py``. References are ``#N`` (the caller's profile) or ``<profile>#N``.
"""

from __future__ import annotations

from .base import Params, Payload, Result
from .common import ProfileParams
from .registry import event, method


class DeckSession(Result):
    """``session_deck._project`` + the host's live runtime id."""

    ref: str
    profile: str
    handle: int
    session_id: str  # compression-lineage root (the durable link)
    tip_session_id: str  # current stored id — what session.resume takes
    title: str
    model: str
    cwd: str
    surface: str
    group_name: str
    parent_handle: int | None = None
    state: str  # running | waiting | idle | detached | dormant
    owner_surface: str
    owner_pid: int | None = None
    live_session_id: str  # runtime id in THIS host ("" when not live here)
    message_count: int
    opened_at: float
    last_active: float
    estimated_cost_usd: float | None = None


class DeckListParams(ProfileParams):
    profiles: list[str] | None = None  # default: every profile on this machine
    include_closed: bool = False


class DeckListResult(Result):
    sessions: list[DeckSession]
    open_count: int


method("deck.list", params=DeckListParams, result=DeckListResult,
       doc="Open sessions across profiles, most recently active first.")


class DeckRegisterParams(Params):
    session_id: str  # live runtime id in this host
    group: str | None = None
    parent: str | None = None  # deck ref of the session that spawned this one


class DeckRegisterResult(Result):
    ref: str
    handle: int
    profile: str


method("deck.register", params=DeckRegisterParams, result=DeckRegisterResult,
       doc="Mark a live session open in the deck: it now survives client disconnects until closed.")


class DeckDetachParams(Params):
    session_id: str  # live runtime id


class DeckDetachResult(Result):
    detached: bool


method("deck.detach", params=DeckDetachParams, result=DeckDetachResult,
       doc="This client stops showing a deck session; the session keeps running (detached when no client is left).")


class DeckTargetParams(ProfileParams):
    """``target`` is a deck ref; ``from_session_id`` (a live runtime id) identifies a SESSION acting as
    the sender, and makes a bare ``#N`` relative to that session's profile."""

    target: str
    from_session_id: str | None = None


class DeckSendParams(DeckTargetParams):
    message: str
    wait_s: float = 0.0  # >0: block up to this long for the target's reply


class DeckSendResult(Result):
    ref: str
    delivery_id: str
    status: str  # queued | claimed | settled | failed | cancelled | ambiguous
    woke: bool  # the target was dormant and the host resumed it to deliver
    reply: str | None = None
    error: str | None = None


method("deck.send", params=DeckSendParams, result=DeckSendResult,
       doc="Queue a message as the target's next user turn (never mid-turn); wakes a dormant target.")


class DeckCloseResult(Result):
    ref: str
    closed: bool


method("deck.close", params=DeckTargetParams, result=DeckCloseResult,
       doc="Close an open session: ends its runtime wherever it lives and removes it from the deck.")


class DeckInterruptResult(Result):
    ref: str
    interrupted: bool


method("deck.interrupt", params=DeckTargetParams, result=DeckInterruptResult,
       doc="Stop the target's running turn (host-owned sessions only).")


class DeckPeekParams(DeckTargetParams):
    limit: int = 12


class DeckMessage(Result):
    role: str
    text: str


class DeckPeekResult(Result):
    session: DeckSession
    messages: list[DeckMessage]


method("deck.peek", params=DeckPeekParams, result=DeckPeekResult,
       doc="The target's row plus its last user/assistant messages.")


class DeckResolveResult(Result):
    session: DeckSession


method("deck.resolve", params=DeckTargetParams, result=DeckResolveResult,
       doc="Resolve a ref to its row (what a client needs to session.resume it with the right profile).")


class DeckChangedPayload(Payload):
    open_count: int


event("deck.changed", DeckChangedPayload, doc="The open-session set or a session's runtime state changed (broadcast).")


class DeckClosedPayload(Payload):
    ref: str
    closed_by: str


event("deck.closed", DeckClosedPayload, doc="This session was closed through the deck; its runtime is ending.")
