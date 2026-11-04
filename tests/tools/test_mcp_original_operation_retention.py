"""Delayed MCP media keeps its original operation across the real background loop.

The RPC transport is synthetic; vault/source checks, loop hop and encrypted readers are real.
"""
import asyncio
import base64
import json
import threading
from types import SimpleNamespace

import pytest

from tests.tools.test_mcp_private_media import _image

pytest_plugins = ["tests.tools.test_mcp_private_media"]


@pytest.fixture
def mcp_background_loop(monkeypatch):
    from tools import mcp_tool

    loop, ready = asyncio.new_event_loop(), threading.Event()

    def serve():
        asyncio.set_event_loop(loop)
        loop.call_soon(ready.set)
        loop.run_forever()

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        assert ready.wait(10)
        monkeypatch.setattr(mcp_tool, "_mcp_loop", loop)
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=10)
        assert not thread.is_alive()
        loop.close()


@pytest.mark.parametrize("change", ["session-close", "vault-reunlock"])
@pytest.mark.parametrize("operation", ["tool", "resource"])
def test_delayed_mcp_media_refuses_original_revocation_and_fresh_sibling_still_works(
        home, monkeypatch, mcp_background_loop, change, operation):
    from agent.session_context import scoped_current_session_id
    from hermes_security import artifacts, vault
    from hermes_security.session_authority import start_session_authority
    from tools import mcp_tool, mcp_tool_handlers

    first_id, sibling_id = "mcp-A-" + change, "mcp-B-" + change
    first, sibling = start_session_authority(home, first_id), start_session_authority(home, sibling_id)
    original = _image("synthetic operation payload") if operation == "tool" else b"synthetic private document"
    encoded = base64.b64encode(original).decode()
    response = (SimpleNamespace(content=[SimpleNamespace(type="image", mimeType="image/png", data=encoded)],
                                isError=False) if operation == "tool" else
                SimpleNamespace(contents=[SimpleNamespace(blob=encoded, uri="memory://private/result.txt",
                                                          mimeType="text/plain")]))
    fresh = False

    async def rpc(*_args, **_kwargs):
        if not fresh:
            if change == "session-close":
                first.close()
            else:
                vault.lock_now(home)
                vault.unlock(home, "synthetic media custody password")
        return response

    server = SimpleNamespace(session=SimpleNamespace(call_tool=rpc, read_resource=rpc), _rpc_lock=asyncio.Lock())
    monkeypatch.setattr(mcp_tool, "_servers", {"operation-media": server})
    handler = (mcp_tool_handlers._make_tool_handler("operation-media", "capture", 20) if operation == "tool"
               else mcp_tool_handlers._make_read_resource_handler("operation-media", 20))
    arguments = {} if operation == "tool" else {"uri": "memory://private/result.txt"}
    try:
        with scoped_current_session_id(first_id):
            result = json.loads(handler(arguments))
        assert "MEDIA:" not in result["result"] and "saved to" not in result["result"]
        unavailable = "could not be retained" if operation == "tool" else "could not be cached"
        assert unavailable in result["result"]
        assert not [path for path in (home / "artifacts").rglob("*") if path.is_file()]
        assert vault.is_unlocked(home)
        if change == "vault-reunlock":
            sibling.close()
            sibling = start_session_authority(home, sibling_id)
        fresh = True
        with scoped_current_session_id(sibling_id):
            result = json.loads(handler(arguments))
        retained = [path for path in (home / "artifacts").rglob("*") if path.is_file()]
        assert len(retained) == 1
        saved = retained[0]
        assert str(saved) in result["result"]
        assert artifacts.read_bytes(saved, home=home) == original
    finally:
        first.close()
        sibling.close()


def test_nested_new_media_operation_cannot_borrow_its_live_outer_session(home):
    from agent.session_context import scoped_current_session_id
    from hermes_security.errors import VaultError
    from hermes_security.session_authority import start_session_authority
    from tools import media_cache

    first = start_session_authority(home, "outer-A")
    sibling = start_session_authority(home, "nested-B")
    try:
        with scoped_current_session_id("outer-A"), media_cache.retention_scope():
            sibling.close()
            with scoped_current_session_id("nested-B"), pytest.raises(VaultError):
                with media_cache.retention_scope():
                    media_cache.cache_document_from_bytes(b"nested private document", "result.txt")
            assert not [path for path in (home / "artifacts").rglob("*") if path.is_file()]
            saved = media_cache.cache_document_from_bytes(b"live outer result", "result.txt")
            from hermes_security import artifacts
            assert artifacts.read_bytes(saved, home=home) == b"live outer result"
    finally:
        first.close()
        sibling.close()
