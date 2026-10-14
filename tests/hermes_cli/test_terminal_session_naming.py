"""Session names reach native terminal chrome without changing model context."""

from types import SimpleNamespace

from hermes_state import SessionDB


def test_name_command_uses_auxiliary_llm_and_persists_to_real_session(tmp_path, monkeypatch):
    from hermes_cli.commands import resolve_command
    from hermes_cli.session_naming import rename_session

    db = SessionDB(tmp_path / "state.db")
    db.create_session("naming-session", source="cli")
    db.append_message("naming-session", "user", "Restore my terminals after reboot")
    messages = db.get_messages_as_conversation("naming-session")
    before = [dict(m) for m in messages]
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"title":"Restore terminal workspace"}', reasoning_content=None))])

    monkeypatch.setattr("agent.title_generator.call_llm", completion)
    for command in ("name", "rename"):
        assert resolve_command(command).name == "name"
    title = rename_session(db, "naming-session", messages, main_runtime={"provider": "custom", "model": "test"})
    assert title == db.get_session_title("naming-session") == "Restore terminal workspace"
    assert calls[0]["task"] == "title_generation"
    assert messages == before == db.get_messages_as_conversation("naming-session")
    db.close()


def test_native_title_is_sanitized_and_only_updated_when_changed():
    from hermes_cli.session_naming import sync_terminal_title

    titles = []
    cli = SimpleNamespace(
        session_id="session-123", _pending_title=None,
        _session_db=SimpleNamespace(get_session_title=lambda _: "Restore\x1b]52;c;payload\x07\nterminals"),
        _app=SimpleNamespace(output=SimpleNamespace(set_title=titles.append, flush=lambda: None)),
    )
    sync_terminal_title(cli)
    sync_terminal_title(cli)
    assert len(titles) == 1
    assert titles[0].startswith("Hermes — Restore")
    assert not any(ord(char) < 32 or ord(char) == 127 for char in titles[0])
