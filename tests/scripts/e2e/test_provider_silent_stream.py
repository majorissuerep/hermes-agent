"""Real LiteLLM wire recovery without replaying a tool effect or losing the cached prefix."""

import json
import secrets
import shlex
import threading

import pytest

from tests.fakes.providers.chat_variants import CText, CTools, FakeChatVariantServer


@pytest.mark.platforms("posix")
def test_silent_proxy_recovers_and_the_next_turn_keeps_the_same_conversation(tmp_path):
    from agent.secret_scope import build_profile_secret_scope, reset_secret_scope, set_secret_scope
    from hermes_cli.config import save_config
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_security import vault
    from hermes_state import SessionDB
    from run_agent import AIAgent

    home, workspace = tmp_path / "profile", tmp_path / "workspace"
    home.mkdir(mode=0o700)
    workspace.mkdir(mode=0o700)
    counter = workspace / "one effect.txt"
    release = threading.Event()
    seen = []

    def respond(record):
        body = record["body"]
        seen.append(body)
        if body.get("stream"):
            # The client must hit its configured 2s read budget before any headers or events.
            release.wait(timeout=15)
            return CText("LOST_STREAM_MUST_NOT_APPEAR")
        release.set()
        last = body["messages"][-1]
        if last["role"] == "tool":
            return CText("FIRST_REPLY")
        if "SECOND_TURN" in str(last.get("content")):
            return CText("SECOND_REPLY")
        return CTools([("terminal", {"command": f"printf ONE >> {shlex.quote(str(counter))}", "timeout": 5})])

    token = set_hermes_home_override(home)
    scope = None
    agent = database = None
    try:
        password = secrets.token_urlsafe(24)
        vault.init_vault(home, password)
        vault.unlock(home, password)
        with FakeChatVariantServer(respond) as server:
            save_config({
                "model": {"provider": "litellm", "default": "acceptance-model", "base_url": server.base_url},
                "providers": {"litellm": {"request_timeout_seconds": 2, "stale_timeout_seconds": 3}},
                "agent": {"api_max_retries": 1, "auto_recovery_cycles": 0},
                "terminal": {"cwd": str(workspace)}, "updates": {"check": False},
                "auxiliary": {"title_generation": {"enabled": False}},
            }, merge_existing=False)
            scope = set_secret_scope(build_profile_secret_scope(home), profile_home=str(home))
            database = SessionDB(home / "state.db")
            database.create_session("silent-proxy", source="cli")
            visible = []
            agent = AIAgent(
                provider="litellm", api_mode="chat_completions", model="acceptance-model",
                base_url=server.base_url, api_key="sk-synthetic-wire", platform="cli",
                session_db=database, session_id="silent-proxy", enabled_toolsets=["terminal"],
                quiet_mode=True, skip_memory=True, skip_context_files=True, skip_background_review=True,
                max_iterations=5, max_tokens=256, reasoning_config={"enabled": False},
                stream_delta_callback=visible.append,
            )
            assert agent.provider == "litellm"
            first = agent.run_conversation("FIRST_TURN: execute the requested terminal action exactly once.")
            assert not first.get("failed"), first
            assert "FIRST_REPLY" in first["final_response"], first
            assert counter.read_text() == "ONE"
            assert agent._disable_streaming is True
            before_second = len(seen)
            second = agent.run_conversation("SECOND_TURN: preserve the previous result.", conversation_history=first["messages"])
            assert not second.get("failed"), second
            assert "SECOND_REPLY" in second["final_response"], second
            assert counter.read_text() == "ONE", "tool side effect was executed again"
            assert len(seen) == 4 and before_second == 3, seen
            assert seen[0]["stream"] is True
            assert not any(body.get("stream") for body in seen[1:])
            assert seen[0]["messages"] == seen[1]["messages"]
            assert seen[0]["tools"] == seen[1]["tools"]
            assert all(body["messages"][0] == seen[0]["messages"][0] for body in seen)
            assert seen[3]["messages"][:len(seen[0]["messages"])] == seen[0]["messages"]
            assert server.invalid_requests() == []
            durable, _display = database.get_resume_conversations("silent-proxy")
            encoded = json.dumps(durable)
            assert "FIRST_REPLY" in encoded and "SECOND_REPLY" in encoded
            assert "LOST_STREAM_MUST_NOT_APPEAR" not in encoded
            assert not (home / "state.db").read_bytes().startswith(b"SQLite format 3")
    finally:
        release.set()
        if agent is not None:
            agent.close()
        if database is not None:
            database.close()
        if scope is not None:
            reset_secret_scope(scope)
        vault.lock_now(home)
        reset_hermes_home_override(token)
