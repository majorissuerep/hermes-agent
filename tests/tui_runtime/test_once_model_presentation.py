"""The native footer and append-only transcript must identify the actual one-turn route."""
import copy
import threading
from types import SimpleNamespace

import pytest


class RuntimeState(SimpleNamespace):
    """No provider I/O: only the adapter's successful state replacement is substituted."""

    def switch_model(self, **target):
        for source, destination in (("new_model", "model"), ("new_provider", "provider"),
                                    ("api_key", "api_key"), ("base_url", "base_url"), ("api_mode", "api_mode")):
            setattr(self, destination, target[source])


@pytest.fixture
def live_state(tmp_path, monkeypatch):
    from hermes_cli.config import save_config
    from hermes_security import vault
    from hermes_state import SessionDB
    from tests.e2e.vault_home import seal_fixture_home

    home = tmp_path / "owner"
    monkeypatch.setenv("HERMES_HOME", str(home))
    seal_fixture_home(home)
    save_config({"updates": {"check": False}, "agent": {"reasoning_effort": "none"}}, merge_existing=False)
    from tui_runtime import server

    emitted = []
    monkeypatch.setattr(server, "_emit", lambda event, sid, payload=None: emitted.append((event, sid, payload)))
    monkeypatch.setattr(server, "_probe_credentials", lambda _agent: None)
    try:
        with SessionDB(home / "state.db") as db:
            db.create_session("conversation", source="tui", model="once/model")
            history = [{"role": "user", "content": "random retained question"},
                       {"role": "assistant", "content": "retained answer"}]
            db.append_messages_batch("conversation", history)
            agent = RuntimeState(model="once/model", provider="openai", api_key="synthetic", base_url="",
                api_mode="chat_completions", reasoning_config={"enabled": False}, session_id="conversation",
                _session_db=db, _cached_system_prompt="frozen SYSTEM prefix")
            session = {"agent": agent, "session_key": "conversation", "history": history,
                       "history_lock": threading.RLock(), "cwd": str(tmp_path), "personality": ""}
            yield server, session, emitted, db
    finally:
        vault.clear_vault_cache()


def test_once_reasoning_updates_the_actual_footer_payload_without_pinning(live_state):
    server, session, emitted, _db = live_state
    server._emit_session_info("sid", session)
    server._apply_switch_reasoning("sid", session, session["agent"], "high", persist_global=False, one_turn=True)

    payload = [p for event, _sid, p in emitted if event == "session.info"][-1]
    assert payload["model"] == "once/model"
    assert payload["reasoning_effort"] == "high"
    assert "create_reasoning_override" not in session
    from hermes_cli.config import load_config_readonly

    assert load_config_readonly()["agent"]["reasoning_effort"] == "none"


def test_once_restore_appends_the_base_route_and_preserves_durable_prefix(live_state):
    server, session, emitted, db = live_state
    from tui_runtime.prompt_turn import _TurnRun

    agent = session["agent"]
    agent.reasoning_config = {"enabled": True, "effort": "high"}
    server._append_model_switch_marker(session, model=agent.model, provider=agent.provider)
    prefix = copy.deepcopy(session["history"])
    durable = db.get_messages_as_conversation("conversation")
    snapshot = {"model": "base/model", "provider": "openai", "api_key": "synthetic", "base_url": "",
                "api_mode": "chat_completions", "reasoning_config": {"enabled": True, "effort": "low"}}

    server._release_turn_scopes("sid", session, _TurnRun(agent, snapshot))

    assert agent.model == "base/model" and agent.reasoning_config["effort"] == "low"
    assert agent._cached_system_prompt == "frozen SYSTEM prefix"
    assert session["history"][:len(prefix)] == prefix
    assert db.get_messages_as_conversation("conversation")[:len(durable)] == durable
    assert len(session["history"]) == len(prefix) + 1
    assert "base/model" in session["history"][-1]["content"]
    assert "base/model" in db.get_messages_as_conversation("conversation")[-1]["content"]
    payload = [p for event, _sid, p in emitted if event == "session.info"][-1]
    assert payload["model"] == "base/model" and payload["reasoning_effort"] == "low"
