"""Private images retain original authority through the real vision pool's handoff.

Only revocation timing and route selection are injected; image I/O, CPU pool and custody are real.
"""
import json
import secrets

import pytest

from tests.tools.test_mcp_private_media import _image

pytest_plugins = ["tests.tools.test_mcp_private_media"]


@pytest.mark.parametrize("change", ["session-close", "vault-reunlock"])
@pytest.mark.parametrize("phase", ["resize", "worker-handoff"])
def test_private_attachment_rechecks_original_custody_and_fresh_sibling_works(home, monkeypatch, change, phase):
    from agent.session_context import scoped_current_session_id
    from hermes_security import artifacts, vault
    from hermes_security.session_authority import start_session_authority
    from tools import media_cache, mcp_tool_content, vision_tools

    first, sibling = start_session_authority(home, "attach-A"), start_session_authority(home, "attach-B")
    resize, prepare = vision_tools._resize_image_for_vision, mcp_tool_content._mcp_native_image_part
    events = []

    def revoke():
        if events:
            return
        events.append(change)
        if change == "session-close":
            first.close()
        else:
            vault.lock_now(home)
            vault.unlock(home, "synthetic media custody password")

    def resize_and_revoke(*args, **kwargs):
        revoke()
        return resize(*args, **kwargs)

    def prepare_and_revoke(path):
        ready = prepare(path)
        revoke()
        return ready

    monkeypatch.setattr(vision_tools, "_should_use_native_vision_fast_path", lambda: True)
    if phase == "resize":
        monkeypatch.setattr(vision_tools, "_resize_image_for_vision", resize_and_revoke)
    else:
        monkeypatch.setattr(mcp_tool_content, "_mcp_native_image_part", prepare_and_revoke)
    original = _image(secrets.token_hex(24))
    try:
        with scoped_current_session_id("attach-A"), media_cache.retention_scope():
            path = media_cache.cache_image_from_bytes(original, ".png")
            result = mcp_tool_content._mcp_result_with_native_images(json.dumps({"result": "MEDIA:" + path}), [path])
        assert events == [change]
        assert isinstance(result, str) and "MEDIA:" in json.loads(result)["result"]
        assert artifacts.read_bytes(path, home=home) == original
        if change == "vault-reunlock":
            sibling.close()
            sibling = start_session_authority(home, "attach-B")
        with scoped_current_session_id("attach-B"), media_cache.retention_scope():
            fresh = mcp_tool_content._mcp_result_with_native_images(json.dumps({"result": "MEDIA:" + path}), [path])
        assert isinstance(fresh, dict) and fresh["_multimodal"] is True
        assert any(part.get("type") == "image_url" for part in fresh["content"])
    finally:
        first.close()
        sibling.close()
