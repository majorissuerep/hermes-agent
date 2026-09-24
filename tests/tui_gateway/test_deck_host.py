"""Session deck host (``tui_gateway/methods_deck.py``) through the real RPC dispatcher: a deck session survives
losing its clients, cross-session messages are pinned to the TARGET's own lease (so only it can run them, as
its next turn), a session cannot act on itself, and a remote close ends the runtime and the deck row."""

import time

import pytest

from tools import bot_live_delivery as mailbox
from tui_gateway import server


def _call(method, **params):
    response = server.handle_request({"id": f"t-{method}", "method": method, "params": params})
    return response.get("result"), response.get("error")


@pytest.fixture
def live(tmp_path):
    made = []

    def make(sid):
        record = server._deferred_session_record(f"stored-{sid}", cols=80, cwd=str(tmp_path), history=[], lease=None)
        with server._sessions_lock:
            server._sessions[sid] = record
        made.append(sid)
        return record

    yield make
    for sid in made:
        with server._sessions_lock:
            record = server._sessions.pop(sid, None)
        if record and record.get("active_session_lease") is not None:
            record["active_session_lease"].release()


def test_deck_session_is_never_reaped_for_losing_its_clients(live, monkeypatch):
    monkeypatch.setattr(server, "_WS_ORPHAN_REAP_GRACE_S", 5.0)
    live("plain")
    live("decked")
    assert _call("deck.register", session_id="decked")[0]["ref"] == "#1"

    server._schedule_ws_orphan_reap("plain")
    server._schedule_ws_orphan_reap("decked")
    try:
        assert "plain" in server._pending_ws_reaps
        assert "decked" not in server._pending_ws_reaps
    finally:
        server._cancel_ws_orphan_reap("plain")


def test_message_is_pinned_to_the_target_lease_and_names_the_sender(live):
    target = live("a")
    live("b")
    _call("deck.register", session_id="a")
    _call("deck.register", session_id="b")

    result, error = _call("deck.send", target="#1", message="please rebase", from_session_id="b")
    assert error is None and result["status"] == "queued" and not result["woke"]

    from hermes_cli.session_deck import profile_home

    ticket = mailbox.read_delivery_result(profile_home("default"), result["delivery_id"])
    assert ticket["owner"]["lease_id"] == target["active_session_lease"].lease_id
    assert ticket["owner"]["live_session_id"] == "a"
    assert "message from session #2" in ticket["message"] and ticket["message"].endswith("please rebase")


@pytest.mark.parametrize("method", ["deck.send", "deck.close", "deck.interrupt"])
def test_a_session_cannot_act_on_itself(live, method):
    live("a")
    _call("deck.register", session_id="a")
    _result, error = _call(method, target="#1", message="x", from_session_id="a") if method == "deck.send" \
        else _call(method, target="#1", from_session_id="a")
    assert error is not None and error["code"] == 4022


def test_remote_close_ends_the_runtime_and_leaves_the_deck(live):
    live("a")
    live("b")
    _call("deck.register", session_id="a")
    _call("deck.register", session_id="b")

    result, error = _call("deck.close", target="#1", from_session_id="b")
    assert error is None and result["closed"] is True

    deadline = time.monotonic() + 10
    while "a" in server._sessions and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "a" not in server._sessions
    listed = _call("deck.list")[0]
    assert [s["ref"] for s in listed["sessions"]] == ["#2"] and listed["open_count"] == 1
