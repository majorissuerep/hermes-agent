"""Session deck host (``deck.*``): open sessions outlive their terminals, and any session can reach any other.

The machine-level ``hermes serve`` host owns deck sessions. A deck session is never reaped for losing its
last client (it parks detached); automatic runtime reclaim (idle TTL, LRU cap, host shutdown) leaves it
OPEN but ``dormant`` — the conversation, its handle and its place in the deck survive, and the next attach
or message resumes it. Only ``deck.close`` / ``/close`` end it.

Messages ride the durable owner-pinned mailbox (``tools/bot_live_delivery.py``): the target's own poller
claims them at an idle boundary and runs each as a normal user turn, so role alternation and the cached
prefix are untouched (``session_notifications._poll_bot_live_delivery_once``).

Bodies are rebound onto server.py's globals at install time (method_ctx.bind_module).
"""

from __future__ import annotations

import types

from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method

# Snapshot cadence: runtime states land in state.db at most this stale; a SIGKILLed host loses nothing
# durable (rows are written on open/close), only the last state label, which liveness overrides anyway.
_DECK_SNAPSHOT_S = 5.0
# How often a live session NOT yet marked deck is checked for an open row (a resume path that never
# called deck.register — Desktop reopening a deck chat, a host restart).
_DECK_ADOPT_RECHECK_S = 30.0
# Unused rows (opened, never prompted, no live runtime) are dropped after this long.
_DECK_UNUSED_TTL_S = 3600.0

_deck_runtime = types.SimpleNamespace(thread=None, last_states={}, adopt_checked={})


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────


def _deck_profile_of_home(home) -> str:
    """Profile name owning ``home`` (None = this host's launch profile)."""
    if not home:
        return _current_profile_name()
    from hermes_cli.profiles import list_profile_names
    from hermes_cli.session_deck import profile_home
    resolved = Path(home).resolve()
    for name in list_profile_names():
        with contextlib.suppress(Exception):
            if profile_home(name) == resolved:
                return name
    return _current_profile_name()


def _deck_runtime_state(sid: str, session: dict) -> str:
    status = _session_live_status(sid, session)
    if status == "working":
        return "running"
    if status == "waiting":
        return "waiting"
    return "idle" if _session_has_live_transport(session) else "detached"


def _deck_live_index() -> dict:
    """``(profile, stored key) -> (sid, session)`` for every live, non-finalized session in this host."""
    with _sessions_lock:
        snapshot = list(_sessions.items())
    index = {}
    for sid, session in snapshot:
        if session.get("_finalized"):
            continue
        key = _session_lookup_key(session, fallback="")
        if key:
            index[(_deck_profile_of_home(session.get("profile_home")), key)] = (sid, session)
    return index


def _deck_row_payload(row: dict, index: dict | None = None) -> dict:
    live = (index if index is not None else _deck_live_index()).get((row["profile"], row["tip_session_id"]))
    state = _deck_runtime_state(*live) if live else row["state"]
    return {
        "ref": row["ref"], "profile": row["profile"], "handle": int(row["handle"]),
        "session_id": row["session_id"], "tip_session_id": row["tip_session_id"],
        "title": row["title"], "model": row["model"], "cwd": row["cwd"], "surface": row["surface"],
        "group_name": row["group_name"], "parent_handle": row["parent_handle"], "state": state,
        "owner_surface": row["owner_surface"], "owner_pid": row["owner_pid"],
        "live_session_id": live[0] if live else "", "message_count": int(row["message_count"]),
        "opened_at": float(row["opened_at"]), "last_active": float(row["last_active"]),
        "estimated_cost_usd": row["estimated_cost_usd"],
    }


def _deck_caller(params: dict) -> tuple:
    """``(sender dict, default profile, sender ref or None)`` for a deck verb. A ``from_session_id`` names a
    live session in this host acting as the sender; otherwise the actor is the user on ``params.profile``."""
    from hermes_cli.session_deck import DeckRef
    sid = str(params.get("from_session_id") or "")
    if sid:
        session = _sessions.get(sid)
        if session is None:
            raise LookupError(f"sender session {sid} is not live in this host")
        mark = session.get("deck")
        profile = _deck_profile_of_home(session.get("profile_home"))
        if not mark:
            return {"name": f"an unregistered session in profile {profile}"}, profile, None
        ref = DeckRef(mark["profile"], mark["handle"])
        key = _session_lookup_key(session, fallback=sid)
        return {"ref": str(ref), "title": _session_live_title(session, key)}, profile, ref
    profile = _response_profile_name(params.get("profile"))
    return {"name": "the user (hermes deck)"}, profile, None


def _deck_target(params: dict) -> tuple:
    """``(sender, sender_ref, target ref, projected row)``; raises LookupError / ValueError."""
    from hermes_cli.session_deck import parse_ref, resolve
    sender, default_profile, sender_ref = _deck_caller(params)
    ref = parse_ref(str(params.get("target") or ""), default_profile=default_profile)
    return sender, sender_ref, ref, resolve(ref)


def _deck_mark(sid: str, session: dict, profile: str, handle: int) -> str | None:
    """Make ``session`` a deck session: persistent across client loss, lease held (so the deck sees it live
    and the mailbox can pin it). Returns the lease refusal message, if any."""
    session["deck"] = {"profile": profile, "handle": int(handle)}
    _cancel_ws_orphan_reap(sid)
    refusal = _ensure_active_session_slot(sid, session)
    _deck_ensure_snapshot_loop()
    return str(refusal) if refusal else None


def _deck_changed() -> None:
    from hermes_cli.session_deck import deck_profiles, open_db
    count = 0
    for profile in deck_profiles():
        with contextlib.suppress(Exception), open_db(profile) as db:
            count += db.deck_open_count()
    _broadcast_global_event("deck.changed", {"open_count": count})


def _deck_wake(profile: str, row: dict) -> str:
    """Resume a dormant deck session headlessly in this host (agent built, no client) and mark it."""
    from tui_gateway.transport import bind_transport, reset_transport
    params = {"session_id": row["tip_session_id"], "eager_build": True, "omit_messages": True}
    if profile != _current_profile_name():
        params["profile"] = profile
    token = bind_transport(_detached_ws_transport)
    try:
        resp = handle_request({"id": f"deck-wake-{uuid.uuid4().hex[:8]}", "method": "session.resume",
                               "params": params})
    finally:
        reset_transport(token)
    if not resp or "error" in resp:
        raise RuntimeError((resp or {}).get("error", {}).get("message") or "resume failed")
    sid = resp["result"]["session_id"]
    session = _sessions.get(sid)
    if session is None:
        raise RuntimeError("resumed session vanished")
    if refusal := _deck_mark(sid, session, profile, row["handle"]):
        raise RuntimeError(refusal)
    return sid


def _deck_end_runtime(sid: str, session: dict, ref: str, closed_by: str) -> None:
    """Tell the session's clients, then tear its runtime down off the RPC thread (teardown waits for a
    running turn to settle)."""
    _emit("deck.closed", sid, {"ref": ref, "closed_by": closed_by})
    session.pop("deck", None)
    _start_session_work(lambda: _close_session_by_id(sid, end_reason="deck_close"),
                        name=f"deck-close-{sid[:8]}", session=session)


# ── snapshot loop ────────────────────────────────────────────────────────────────────────────────


def _deck_ensure_snapshot_loop() -> None:
    rt = _deck_runtime
    if rt.thread is not None and rt.thread.is_alive():
        return
    rt.thread = threading.Thread(target=_deck_snapshot_loop, name="deck-snapshot", daemon=True)
    rt.thread.start()


def _deck_snapshot_loop() -> None:
    while True:
        time.sleep(_DECK_SNAPSHOT_S)
        try:
            _deck_snapshot_once()
        except Exception:
            logger.debug("deck snapshot pass failed", exc_info=True)


def _deck_snapshot_once() -> None:
    """One pass: adopt live sessions that belong to open rows, persist runtime states, end runtimes whose row
    was closed elsewhere, prune never-used rows, and broadcast when anything moved."""
    from hermes_cli.session_deck import DeckRef, deck_profiles, open_db
    rt, now = _deck_runtime, time.time()
    index = _deck_live_index()
    by_profile: dict = {}
    for (profile, key), (sid, session) in index.items():
        by_profile.setdefault(profile, []).append((key, sid, session))
    changed = False
    for profile in deck_profiles():
        live = by_profile.get(profile, [])
        with open_db(profile) as db:
            states = []
            for key, sid, session in live:
                mark = session.get("deck")
                if mark is None:
                    if now - rt.adopt_checked.get(sid, 0.0) < _DECK_ADOPT_RECHECK_S:
                        continue
                    rt.adopt_checked[sid] = now
                    row = db.deck_row_for_session(key)
                    if row is None or row["closed_at"] is not None:
                        continue
                    _deck_mark(sid, session, profile, row["handle"])
                    changed = True
                else:
                    row = db.deck_row(mark["handle"])
                    if row is not None and row["closed_at"] is not None:
                        _deck_end_runtime(sid, session, str(DeckRef(profile, row["handle"])), row["closed_by"] or "")
                        changed = True
                        continue
                states.append((key, _deck_runtime_state(sid, session)))
            db.deck_snapshot(states)
            for key, state in states:
                if rt.last_states.get((profile, key)) != state:
                    rt.last_states[(profile, key)] = state
                    changed = True
            if db.deck_prune_unused(older_than_s=_DECK_UNUSED_TTL_S, keep=[k for k, _s, _x in live]):
                changed = True
    live_sids = {sid for sid, _s in index.values()}
    rt.adopt_checked = {sid: ts for sid, ts in rt.adopt_checked.items() if sid in live_sids}
    if changed:
        _deck_changed()


# ── RPC ──────────────────────────────────────────────────────────────────────────────────────────


@method("deck.list")
def _(rid, params: dict) -> dict:
    from hermes_cli.session_deck import list_deck
    try:
        rows = list_deck(params.get("profiles"), include_closed=bool(params.get("include_closed")))
    except Exception as exc:
        return _err(rid, 5000, f"deck unavailable: {exc}")
    index = _deck_live_index()
    sessions = [_deck_row_payload(row, index) for row in rows]
    return _ok(rid, {"sessions": sessions, "open_count": sum(1 for r in rows if r["closed_at"] is None)})


@method("deck.register")
def _(rid, params: dict) -> dict:
    from hermes_cli.session_deck import DeckRef, open_db, parse_ref, resolve
    sid = str(params.get("session_id") or "")
    session = _sessions.get(sid)
    if session is None or session.get("_finalized"):
        return _err(rid, 4001, "session not found")
    profile = _deck_profile_of_home(session.get("profile_home"))
    parent_handle = None
    if parent := str(params.get("parent") or ""):
        try:
            parent_ref = parse_ref(parent, default_profile=profile)
            parent_handle = resolve(parent_ref)["handle"] if parent_ref.profile == profile else None
        except (LookupError, ValueError):
            parent_handle = None
    key = _session_lookup_key(session, fallback=sid)
    with open_db(profile) as db:
        handle = db.deck_open(key, surface=_session_source(session) or "tui", cwd=str(session.get("cwd") or ""),
                              group=str(params.get("group") or ""), parent_handle=parent_handle)
    if refusal := _deck_mark(sid, session, profile, handle):
        return _err(rid, 4029, refusal)
    _deck_changed()
    return _ok(rid, {"ref": str(DeckRef(profile, handle)), "handle": handle, "profile": profile})


@method("deck.resolve")
def _(rid, params: dict) -> dict:
    try:
        _sender, _sref, _ref, row = _deck_target(params)
    except (LookupError, ValueError) as exc:
        return _err(rid, 4044, str(exc))
    return _ok(rid, {"session": _deck_row_payload(row)})


@method("deck.peek")
def _(rid, params: dict) -> dict:
    from hermes_cli.session_deck import tail
    try:
        _sender, _sref, ref, row = _deck_target(params)
    except (LookupError, ValueError) as exc:
        return _err(rid, 4044, str(exc))
    limit = max(1, min(int(params.get("limit") or 12), 100))
    messages = tail(ref.profile, row["tip_session_id"], limit) if row["started"] else []
    return _ok(rid, {"session": _deck_row_payload(row), "messages": messages})


@method("deck.send")
def _(rid, params: dict) -> dict:
    from hermes_cli.session_deck import frame_message, live_owner, profile_home, sender_author
    from tools.bot_live_delivery import await_delivery, deliver_to_live_owner, live_owner_pin
    message = str(params.get("message") or "").strip()
    if not message:
        return _err(rid, 4016, "message required")
    try:
        sender, sender_ref, ref, row = _deck_target(params)
    except (LookupError, ValueError) as exc:
        return _err(rid, 4044, str(exc))
    if sender_ref == ref:
        return _err(rid, 4022, "a session cannot message itself")
    home = profile_home(ref.profile)
    pin = live_owner_pin(live_owner(ref.profile, row["tip_session_id"]))
    woke = False
    if pin is None:
        owner = live_owner(ref.profile, row["tip_session_id"])
        if owner is not None:
            return _err(rid, 4023, f"{ref} is held by a {owner.get('surface') or 'foreign'} process "
                        f"(pid {owner.get('pid')}) that cannot receive messages")
        try:
            _deck_wake(ref.profile, row)
        except Exception as exc:
            return _err(rid, 5023, f"could not wake {ref}: {exc}")
        woke = True
        pin = live_owner_pin(live_owner(ref.profile, row["tip_session_id"]))
        if pin is None:
            return _err(rid, 5023, f"{ref} woke but holds no deliverable lease")
    record = deliver_to_live_owner(home, pin, frame_message(message, sender), author=sender_author(sender))
    wait_s = max(0.0, min(float(params.get("wait_s") or 0.0), 3600.0))
    if wait_s:
        record = await_delivery(home, record["delivery_id"], wait_s) or record
    if woke:
        _deck_changed()
    return _ok(rid, {"ref": str(ref), "delivery_id": record["delivery_id"], "status": record["status"],
                     "woke": woke, "reply": record.get("reply") or None, "error": record.get("error") or None})


@method("deck.close")
def _(rid, params: dict) -> dict:
    from hermes_cli.session_deck import close_row, sender_label
    try:
        sender, sender_ref, ref, row = _deck_target(params)
    except (LookupError, ValueError) as exc:
        return _err(rid, 4044, str(exc))
    if sender_ref == ref:
        return _err(rid, 4022, "a session cannot close itself through the deck; use /close")
    closed_by = sender_label(sender)
    closed = close_row(ref, closed_by=closed_by)
    if closed and (live := _deck_live_index().get((ref.profile, row["tip_session_id"]))):
        _deck_end_runtime(live[0], live[1], str(ref), closed_by)
    if closed:
        _deck_changed()
    return _ok(rid, {"ref": str(ref), "closed": closed})


@method("deck.interrupt")
def _(rid, params: dict) -> dict:
    try:
        _sender, sender_ref, ref, row = _deck_target(params)
    except (LookupError, ValueError) as exc:
        return _err(rid, 4044, str(exc))
    if sender_ref == ref:
        return _err(rid, 4022, "a session cannot interrupt itself")
    live = _deck_live_index().get((ref.profile, row["tip_session_id"]))
    interrupted = bool(live and live[1].get("running") and _interrupt_session_turn(live[0], live[1]))
    return _ok(rid, {"ref": str(ref), "interrupted": interrupted})


def register(server) -> None:
    """Rebind this module's handlers onto the server namespace."""
    bind_module(globals(), server, skip=("_",))
