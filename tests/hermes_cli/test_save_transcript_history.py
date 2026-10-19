"""Native /save md carries display history; /save json retains every stored row."""
import contextlib
import io
from datetime import datetime
from types import SimpleNamespace

import pytest


def _compacted_store(path):
    from hermes_state import SessionDB

    db = SessionDB(db_path=path)
    db.create_session("s1", "telegram")
    for i in range(1, 7):
        db.append_message("s1", "user", f"question {i}")
        db.append_message("s1", "assistant", f"answer {i}")
    tail = [{"role": "user", "content": "question 6"}, {"role": "assistant", "content": "answer 6"}]
    db.archive_and_compact(
        "s1", [{"role": "user", "content": "[CONTEXT COMPACTION] summary"}, *tail],
        watermark=db.get_active_message_watermark("s1"), tail_count=len(tail),
    )
    return db


def _cli_save(db, fmt, out):
    import cli

    stub = SimpleNamespace(_session_db=db, session_id="s1", conversation_history=[], model="m",
                           session_start=datetime(2026, 1, 1))
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        cli.HermesCLI.save_conversation(stub, f"/save {fmt} {out}")
    return out.read_text(encoding="utf-8") if out.exists() else printed.getvalue()


def _tui_save(db, fmt, out, *, profile=False):
    """Native private runtime saves the owning profile, not launch-home state."""
    import os
    import threading
    from unittest import mock
    from tui_runtime import server

    assert fmt == "json"
    session = {"agent": SimpleNamespace(model="m", session_id="s1"), "session_key": "s1", "history": [],
               "profile_home": str(out.parent), "history_lock": threading.Lock()}
    launch = {"HERMES_HOME": str(out.parent / "launch")} if profile else {}
    with mock.patch.dict(server._sessions, {"tui-save": session}), mock.patch.dict(os.environ, launch):
        resp = server._methods["session.save"]("1", {"session_id": "tui-save"})
    if "error" in resp:
        assert resp["error"]["code"] == 4131
        return resp["error"]["message"]
    return open(resp["result"]["file"], encoding="utf-8").read()


@pytest.mark.parametrize("save", [_cli_save], ids=["cli"])
def test_save_transcript_holds_display_history(tmp_path, monkeypatch, save):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    db = _compacted_store(tmp_path / "state.db")
    try:
        text = save(db, "md", tmp_path / "saved.md")
    finally:
        db.close()
    assert [f"answer {i}" in text for i in range(1, 7)] == [True] * 6


@pytest.mark.parametrize("save", [_cli_save, _tui_save, lambda *a: _tui_save(*a, profile=True)],
                         ids=["cli", "tui", "tui-profile"])
def test_save_json_restores_compacted_history_as_archived(tmp_path, monkeypatch, save):
    """/save json is the snapshot the dashboard import restores: the turns compaction archived come back
    in the display history, and stay out of the live context."""
    import json

    from hermes_state import SessionDB

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    db = _compacted_store(tmp_path / "state.db")
    # A model-only live row (a micro-compaction merge) is context, not display history: only the transfer
    # projection carries it, so the snapshot's ids pin that projection, not the display read.
    db.append_message("s1", "user", "merged", display_metadata={"model_only": True})
    db.append_message("s1", "user", "undone secret")
    db.rewind_to_message("s1", db.get_messages("s1")[-1]["id"])

    def shape(store, **flags):
        return [(m["role"], m["content"]) for m in store.get_messages("s1", **flags)]

    try:
        shown, live = shape(db, include_compacted=True), shape(db)
        snapshot = json.loads(save(db, "json", tmp_path / "saved.json"))
        kept = [m["id"] for m in db.get_messages("s1", include_inactive=True) if m["active"] or m["compacted"]]
        assert [m["id"] for m in snapshot["messages"]] == kept  # every kept row, not the deduped display read
        assert sum(snapshot["timings"]["role_counts"].values()) == len(kept)  # timings name no undone row
        assert {i[k] for i in snapshot["timings"]["intervals"] for k in ("from_message_id", "to_message_id")} <= set(kept)
        # Like `hermes sessions export`, the in-memory backup is capped per session (the session profile's
        # sessions.max_export_messages).
        (tmp_path / "config.yaml").write_text("sessions:\n  max_export_messages: 5\n", encoding="utf-8")
        assert "max_export_messages" in save(db, "json", tmp_path / "capped.json")
    finally:
        db.close()
    restored = SessionDB(db_path=tmp_path / "restored.db")
    try:
        assert restored.import_sessions([snapshot])["ok"]
        assert shape(restored, include_compacted=True) == shown
        assert shape(restored) == live
    finally:
        restored.close()
