"""Session deck registry (``hermes_state_deck.py``): one open row per CONVERSATION, stable human handles,
and an open conversation is never garbage-collected behind the user's back."""

from hermes_state import SessionDB


def _db(tmp_path):
    return SessionDB(db_path=tmp_path / "state.db")


def test_handles_are_stable_per_conversation_and_never_skip(tmp_path):
    db = _db(tmp_path)
    first = db.deck_open("a", surface="tui")
    # Re-registering (reconnect, resume, a second terminal) must not burn handles.
    assert [db.deck_open("a", surface="tui") for _ in range(3)] == [first] * 3
    assert db.deck_open("b", surface="cli") == first + 1


def test_compression_continuation_keeps_its_handle(tmp_path):
    db = _db(tmp_path)
    db.create_session("root", source="tui")
    handle = db.deck_open("root", surface="tui")
    db.end_session("root", "compression")
    db.create_session("tip", source="tui", parent_session_id="root")

    assert db.deck_open("tip", surface="tui") == handle
    row = db.deck_row(handle)
    assert (row["session_id"], row["tip_session_id"]) == ("root", "tip")
    assert db.deck_row_for_session("tip")["handle"] == handle


def test_first_close_wins_and_reopen_keeps_the_address(tmp_path):
    db = _db(tmp_path)
    handle = db.deck_open("a", surface="tui")
    assert db.deck_close(handle, reason="deck_close", closed_by="session #2")
    assert not db.deck_close(handle, reason="deck_close", closed_by="someone else")
    assert db.deck_row(handle)["closed_by"] == "session #2"
    assert db.deck_open_count() == 0

    assert db.deck_open("a", surface="cli") == handle
    assert db.deck_open_count() == 1


def test_snapshot_never_revives_a_closed_row(tmp_path):
    db = _db(tmp_path)
    handle = db.deck_open("a", surface="tui")
    db.deck_close(handle, reason="deck_close")
    assert db.deck_snapshot([("a", "running")]) == 0
    assert db.deck_row(handle)["state"] == "dormant"


def test_prune_spares_open_deck_lineage_but_not_closed(tmp_path):
    db = _db(tmp_path)
    for sid, parent in (("open-root", None), ("open-tip", "open-root"), ("closed", None), ("plain", None)):
        db.create_session(sid, source="cli", parent_session_id=parent)
        db.end_session(sid, "cli_close")
    db._execute_write(lambda c: c.execute("UPDATE sessions SET started_at = 0, last_activity_at = 0"))
    db.deck_open("open-root", surface="cli")
    db.deck_close(db.deck_open("closed", surface="cli"), reason="deck_close")

    candidates = {row["id"] for row in db.list_prune_candidates(older_than_days=1)}
    assert candidates == {"closed", "plain"}
