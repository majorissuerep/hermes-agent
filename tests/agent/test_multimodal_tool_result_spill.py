"""Oversized TEXT parts inside a multimodal tool envelope must go through the same persistence
policy as string results (#95429): a ``browser_exec`` call that captured a screenshot bakes its
whole stdout into the envelope's text part, and that used to bypass ``maybe_persist_tool_result``
and ride every later provider request inline (760K chars -> multi-megabyte requests)."""

import copy
from pathlib import Path
import secrets
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hermes_security import artifacts, vault
from tools.tool_result_storage import PERSISTED_OUTPUT_TAG, extract_persisted_path
from tests.agent.test_tool_call_incremental_persistence import _make_agent, _mock_tool_call


@pytest.fixture
def sealed_agent(tmp_path, monkeypatch):
    home = tmp_path / "owning-profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(home, "synthetic multimodal archive password")
    vault.unlock(home, "synthetic multimodal archive password")
    agent = _make_agent()
    try:
        assert Path(agent.logs_dir) == home / "sessions"
        yield home, agent
    finally:
        agent.close()
        vault.clear_vault_cache()


def _run_sequential(agent, function_result):
    tool_calls = [_mock_tool_call(name="browser_exec", call_id="call_browser")]
    messages: list = []
    assistant_message = SimpleNamespace(content="", tool_calls=tool_calls)
    agent._flush_messages_to_session_db = MagicMock()
    # Vision-capable route (the reporter's case): the envelope stays a part LIST in history, which
    # the per-turn aggregate budget cannot measure — so only per-part persistence bounds it.
    with (patch("model_tools.handle_function_call", return_value=function_result),
          patch.object(type(agent), "_model_supports_vision", lambda self: True),
          patch.object(type(agent), "_provider_supports_vision_tool_messages", lambda self: True)):
        agent._execute_tool_calls_sequential(assistant_message, messages, "task-1")
    return [m for m in messages if m.get("role") == "tool"]


def _envelope(text: str) -> dict:
    # Real browser_exec shape: the content text carries an extra screenshot note the summary lacks.
    return {"_multimodal": True, "text_summary": text, "meta": {"screenshot_path": "/tmp/shot.png"},
            "content": [{"type": "text", "text": text + "\nscreenshot captured"},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}}]}


def test_oversized_multimodal_text_part_is_spilled_and_recoverable(sealed_agent):
    home, agent = sealed_agent
    canary = "synthetic private multimodal result " + secrets.token_hex(16)
    big = canary + "x" * (760_396 - len(canary))
    envelope = _envelope(big)
    original = copy.deepcopy(envelope)
    (tool_msg,) = _run_sequential(agent, envelope)
    content = tool_msg["content"]
    assert isinstance(content, list)
    texts = [p["text"] for p in content if isinstance(p, dict) and p.get("type") == "text"]
    assert len(texts) == 1 and PERSISTED_OUTPUT_TAG in texts[0] and len(texts[0]) < 10_000
    assert [p for p in content if p.get("type") == "image_url"] == [original["content"][1]]
    # One spill file holding the full content text (not overwritten by a second text_summary write),
    # and the duplicate-result stub guard knows where it lives.
    (spilled,) = (home / "artifacts" / "tool-results").glob("*")
    assert extract_persisted_path(texts[0]) == str(spilled)
    ciphertext = spilled.read_bytes()
    assert ciphertext.startswith(b"HRMVAULT\x00") and canary.encode() not in ciphertext
    assert artifacts.read_text(spilled, home=home) == big + "\nscreenshot captured"
    assert agent._tool_guardrails._persisted_result_paths == {"call_browser": str(spilled)}
    assert envelope == original


def test_normal_multimodal_result_is_unchanged(sealed_agent):
    home, agent = sealed_agent
    small = "snapshot ok"
    (tool_msg,) = _run_sequential(agent, _envelope(small))
    assert tool_msg["content"][0] == {"type": "text", "text": small + "\nscreenshot captured"}
    assert not (home / "artifacts" / "tool-results").exists()
