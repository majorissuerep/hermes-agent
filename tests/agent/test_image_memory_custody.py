"""Auxiliary fallback and compression keep private inline pixels out of temporary files."""
from __future__ import annotations

import base64
from io import BytesIO
import json
import tempfile

from PIL import Image


def _inline(width):
    output = BytesIO()
    Image.new("RGB", (width, 1), (137, 17, 201)).save(output, "PNG")
    raw = output.getvalue()
    return raw, "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def test_inline_auxiliary_description_uses_original_bytes_without_a_temporary_file(tmp_path, monkeypatch):
    from agent.vision_message_prep import VisionMessagePrepMixin

    scratch = tmp_path / "private-scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    _, url = _inline(12)
    observed = []

    async def describe(image_url, user_prompt):
        observed.append(image_url)
        assert list(scratch.iterdir()) == []
        assert image_url == url
        return json.dumps({"success": True, "analysis": "private caption 48fe"})

    monkeypatch.setattr("tools.vision_tools.vision_analyze_tool", describe)
    agent = VisionMessagePrepMixin()
    agent._anthropic_image_fallback_cache = {}
    note = agent._describe_image_for_anthropic_fallback(url, "tool")
    assert "private caption 48fe" in note
    assert agent._describe_image_for_anthropic_fallback(url, "tool") == note
    assert observed == [url]
    assert list(scratch.iterdir()) == []


def test_compression_resizes_inline_pixels_in_memory_without_a_temporary_file(tmp_path, monkeypatch):
    from agent.conversation_compression import _shrink_data_url

    scratch = tmp_path / "private-scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    raw, original = _inline(1024)
    _, smaller = _inline(16)
    observed = []

    def resize(data, **kwargs):
        observed.append(data)
        assert list(scratch.iterdir()) == []
        assert data == raw
        assert kwargs["max_dimension"] == 128
        return smaller

    result, unshrinkable = _shrink_data_url(original, max_dimension=128, resize_fn=resize)
    assert result == smaller and unshrinkable is False
    assert observed == [raw] and list(scratch.iterdir()) == []
