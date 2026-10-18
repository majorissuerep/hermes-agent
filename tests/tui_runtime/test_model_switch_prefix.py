"""Switching models appends a pivot without rewriting cached or durable conversation rows."""
import copy
import threading
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("merged_user", [False, True], ids=["standalone-pivot", "pivot-with-user-text"])
def test_model_switch_preserves_sent_prefix_in_memory_and_sqlcipher(tmp_path, monkeypatch, merged_user):
    from hermes_cli.config import ensure_hermes_home
    from hermes_security import migrate, vault
    from hermes_state import SessionDB
    from tui_runtime import server

    home = tmp_path / "encrypted"
    monkeypatch.setenv("HERMES_HOME", str(home))
    ensure_hermes_home()
    assert migrate.migrate_home(home, "synthetic-test-only-unlock").ok
    try:
        with SessionDB(home / "state.db") as db:
            db.create_session("prefix-session", source="tui")
            session = {"session_key": "prefix-session", "history": [], "history_lock": threading.RLock(),
                       "agent": SimpleNamespace(_session_db=db, session_id="prefix-session")}
            server._append_model_switch_marker(session, model="fixture-alpha", provider="fixture")
            if merged_user:
                session["history"][0]["content"] += "\n\nrandom-user-history-must-survive"
                db.append_message(session_id="prefix-session", role="user", content="random-user-history-must-survive")
            session["history"].append({"role": "assistant", "content": "retained answer"})
            db.append_message(session_id="prefix-session", role="assistant", content="retained answer")
            memory_prefix = copy.deepcopy(session["history"])
            durable_prefix = db.get_messages_as_conversation("prefix-session")

            server._append_model_switch_marker(session, model="fixture-beta", provider="fixture")

            assert session["history"][:len(memory_prefix)] == memory_prefix
            assert db.get_messages_as_conversation("prefix-session")[:len(durable_prefix)] == durable_prefix
            assert session["history"][-1]["role"] == "user"
            assert "fixture-beta" in session["history"][-1]["content"]
    finally:
        vault.clear_vault_cache()
