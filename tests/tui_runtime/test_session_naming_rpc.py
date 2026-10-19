"""Native naming RPCs retain profile, shutdown and newer-user-name ownership."""
import contextlib
import threading
from types import SimpleNamespace

import pytest


@contextlib.contextmanager
def _database(home):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from hermes_security import migrate, vault
    from hermes_state import SessionDB

    home.mkdir()
    (home / ".env").write_text("TITLE_KEY=profile-title-credential\n")
    assert migrate.migrate_home(home, "synthetic-naming-fixture").ok
    vault.unlock(home, "synthetic-naming-fixture")
    token = set_hermes_home_override(home)
    db = SessionDB(home / "state.db")
    try:
        db.create_session("stored", source="tui")
        db.append_message("stored", "user", "Keep this original conversation")
        db.set_session_title("stored", "Original")
        yield db
    finally:
        db.close()
        reset_hermes_home_override(token)
        vault.lock_now(home)


def _install_session(server, monkeypatch, db, home=None):
    session = {"session_key": "stored", "profile_home": str(home) if home else None,
               "history_lock": threading.RLock(), "pending_title": None, "agent": None}
    monkeypatch.setattr(server, "_sessions", {"live": session})
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_emit_session_info_for_session", lambda *args: None)
    return session


@pytest.mark.parametrize("newer_action", ["manual", "same", "aba", "close"])
def test_generated_name_does_not_block_dispatch_or_outlive_owner(tmp_path, monkeypatch, newer_action):
    from tui_runtime import server

    entered, release, returned, answered = (threading.Event() for _ in range(4))
    responses = []

    def complete(**kwargs):
        entered.set()
        assert release.wait(10)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"title":"Late generated title"}', reasoning_content=None))])

    monkeypatch.setattr("agent.title_generator.call_llm", complete)
    with _database(tmp_path / "home") as db:
        session = _install_session(server, monkeypatch, db)
        before = db.get_messages_as_conversation("stored")
        transport = SimpleNamespace(write=lambda row: (responses.append(row), answered.set()))

        def dispatch():
            result = server.dispatch({"id": 1, "method": "session.title", "params": {
                "session_id": "live", "generate": True}}, transport)
            if result is not None:
                transport.write(result)
            returned.set()

        caller = threading.Thread(target=dispatch)
        caller.start()
        try:
            assert entered.wait(5)
            assert returned.wait(2), "naming blocked the stdio request dispatcher"
            if newer_action != "close":
                manual_title = "Original" if newer_action == "same" else "Newer user title"
                response = server.handle_request({"id": 2, "method": "session.title", "params": {
                    "session_id": "live", "title": manual_title}})
                assert response["result"]["title"] == manual_title
                if newer_action == "aba":
                    response = server.handle_request({"id": 3, "method": "session.title", "params": {
                        "session_id": "live", "title": "Original"}})
                    assert response["result"]["title"] == "Original"
            else:
                session["_finalized"] = True
            release.set()
            assert answered.wait(5)
            assert "error" in responses[0], responses
            assert db.get_session_title("stored") == (
                "Newer user title" if newer_action == "manual" else "Original")
            assert db.get_messages_as_conversation("stored") == before
        finally:
            release.set()
            caller.join(10)
            answered.wait(5)
            server._sessions.pop("live", None)


def test_naming_uses_owning_profile_and_returns_canonical_persisted_title(tmp_path, monkeypatch):
    from agent.secret_scope import get_secret
    from hermes_constants import get_hermes_home, set_hermes_home_override, reset_hermes_home_override
    from tui_runtime import server

    home = tmp_path / "profile"
    with _database(home) as db:
        _install_session(server, monkeypatch, db, home)
        seen = []

        def complete(**kwargs):
            seen.append((get_hermes_home(), get_secret("TITLE_KEY")))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content='{"title":"Owned profile title"}', reasoning_content=None))])

        monkeypatch.setattr("agent.title_generator.call_llm", complete)
        token = set_hermes_home_override(tmp_path / "launch")
        try:
            response = server.handle_request({"id": 1, "method": "session.title", "params": {
                "session_id": "live", "generate": True}})
            assert response["result"]["title"] == "Owned profile title", response
            assert seen == [(home, "profile-title-credential")]
            response = server.handle_request({"id": 2, "method": "session.title", "params": {
                "session_id": "live", "title": "  Unicode 名称\n  workspace  "}})
            assert response["result"]["title"] == db.get_session_title("stored") == "Unicode 名称 workspace"
        finally:
            reset_hermes_home_override(token)
            server._sessions.pop("live", None)
