"""A queued model and its reasoning scope are previewed together after settlement."""
import copy
from types import SimpleNamespace

import pytest

from tui_runtime import server


@pytest.mark.parametrize("requested,original,expected", [
    ("high", {"enabled": False}, "high"),
    ("none", {"enabled": True, "effort": "high"}, "none"),
    ("disabled", {"enabled": True, "effort": "high"}, "none"),
    ("HIGH", {"enabled": False}, "high"),
    ("", {"enabled": True, "effort": "high"}, "high"),
])
def test_busy_model_switch_previews_effort_without_mutating_active_turn(
        monkeypatch, tmp_path, requested, original, expected):
    agent = SimpleNamespace(
        model="old-fixture", provider="openai", reasoning_config=copy.deepcopy(original),
        service_tier=None, request_overrides={}, session_id="preview-session",
        api_mode="chat_completions", base_url="https://api.openai.com/v1",
    )
    session = {"agent": agent, "session_key": "preview-session", "running": True}
    monkeypatch.setitem(server._sessions, "preview", session)
    monkeypatch.setattr(server, "_pending_switch_selection_warning", lambda *a: None)
    monkeypatch.setattr(server, "_display_cfg", dict)
    monkeypatch.setattr(server, "_display_session_cwd", lambda _: str(tmp_path))

    def refuse_active_mutation(*a, **kw):
        raise AssertionError("A queued choice must leave the running agent untouched")

    monkeypatch.setattr(server, "_apply_model_switch", refuse_active_mutation)
    command = "gpt-5.4 --once" + (f" --reasoning {requested}" if requested else "")
    result = server.handle_request({"id": "preview-request", "method": "config.set",
                                    "params": {"session_id": "preview", "key": "model", "value": command}})
    assert result["result"]["deferred"] is True
    assert session["pending_model_switch"]["raw"] == command
    # The previous response settles before the next user turn starts.
    session["running"] = False
    info = server._session_info(agent, session)
    assert info["model"] == "gpt-5.4"
    assert info["reasoning_effort"] == expected
    assert info["reasoning_effort_wire"] == ("" if expected == "none" else expected)
    assert agent.model == "old-fixture" and agent.reasoning_config == original
