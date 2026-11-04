"""Synthetic RPC only: original sessionless profile must remain the selected profile."""

import asyncio
import json
from types import SimpleNamespace
import pytest

pytest_plugins = [
    "tests.tools.test_mcp_private_media",
    "tests.tools.test_mcp_original_operation_retention",
]


@pytest.mark.parametrize("retarget", [False, True])
def test_sessionless_request_cannot_retarget_home_during_approval(
    home, tmp_path, monkeypatch, mcp_background_loop, retarget
):
    from agent.session_context import scoped_current_session_id
    from hermes_constants import (
        get_hermes_home,
        set_hermes_home_override,
        reset_hermes_home_override,
    )
    from tools import mcp_tool, mcp_tool_handlers as handlers

    first = tmp_path / "sessionless-missing-owner"
    first.mkdir()
    tokens = [set_hermes_home_override(first)]
    calls = []

    async def rpc(*_args, **_kwargs):
        calls.append(get_hermes_home().resolve())
        return SimpleNamespace(
            isError=False, content=[SimpleNamespace(type="text", text="accepted")]
        )

    server = SimpleNamespace(
        session=SimpleNamespace(call_tool=rpc), _rpc_lock=asyncio.Lock()
    )

    def approve(*_args):
        if retarget:
            tokens.append(set_hermes_home_override(home))

    def acquire(*_args):
        return server, None

    monkeypatch.setattr(handlers, "_trust_gate_check", approve)
    monkeypatch.setattr(handlers, "_acquire_call_server", acquire)
    monkeypatch.setattr(mcp_tool, "_bump_server_error", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mcp_tool, "_reset_server_error", lambda *_args, **_kwargs: None)
    handler = handlers._make_tool_handler("heldout-sessionless", "write", 10)
    try:
        with scoped_current_session_id(""):
            result = json.loads(handler({}))
        if retarget:
            assert calls == [], (
                "Original sessionless request dispatched after its selected home changed"
            )
            assert result.get("error")
        else:
            assert calls == [first.resolve()] and result["result"] == "accepted"
    finally:
        for token in reversed(tokens):
            reset_hermes_home_override(token)
