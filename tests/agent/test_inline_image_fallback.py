"""Malformed and oversized inline vision fallback never leaves plaintext scratch."""
from __future__ import annotations

import tempfile

from agent.vision_message_prep import VisionMessagePrepMixin


def test_corrupt_inline_image_returns_a_description_failure_without_files(tmp_path, monkeypatch):
    scratch = tmp_path / "private-scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    agent = VisionMessagePrepMixin()
    agent._anthropic_image_fallback_cache = {}
    result = agent._describe_image_for_anthropic_fallback(
        "data:image/png;base64,!!!not-valid-base64!!!", "user")
    assert "failed" in result.lower() or "invalid" in result.lower()
    assert list(scratch.iterdir()) == []


def test_oversized_inline_image_is_refused_before_the_auxiliary_call(tmp_path, monkeypatch):
    scratch = tmp_path / "private-scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    agent = VisionMessagePrepMixin()
    agent._anthropic_image_fallback_cache = {}
    monkeypatch.setattr(agent, "_MAX_DATA_URL_BASE64_BYTES", 16)

    async def unavailable(**_kwargs):
        raise AssertionError("Oversized inline bytes must not reach the provider")

    monkeypatch.setattr("tools.vision_tools.vision_analyze_tool", unavailable)
    result = agent._describe_image_for_anthropic_fallback("data:image/png;base64," + "A" * 20, "user")
    assert "size limit" in result
    assert list(scratch.iterdir()) == []
