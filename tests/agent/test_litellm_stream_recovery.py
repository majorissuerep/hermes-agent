"""A silent proxy stream gets one safe, same-payload non-streaming recovery."""

from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError

from agent import chat_completion_helpers as h
from tests.agent.test_stream_unmask_5xx import _make_call


def _proxy_call():
    messages = [{"role": "user", "content": "frozen oracle"}]
    tools = [{"type": "function", "function": {"name": "write_once", "parameters": {}}}]
    call = _make_call({"model": "native", "messages": messages, "tools": tools, "stream": True})
    call.agent.provider = "litellm"
    call.agent.base_url = "http://10.20.30.40/v1"
    call.agent._emit_warning = call.buffered.append
    call._request_route = call._live_route()
    call.first_delta_fired["done"] = False
    call.clients = SimpleNamespace(diag={"chunks": 0, "first_chunk_at": None})
    return call


def _read_timeout():
    request = httpx.Request("POST", "http://10.20.30.40/v1/chat/completions")
    error = APITimeoutError(request=request)
    error.__cause__ = httpx.ReadTimeout("no response headers", request=request)
    return error


@pytest.mark.parametrize("provider,read,stale", [("litellm", 180.0, 180.0), ("ollama", 1800.0, 900.0)])
def test_proxy_address_does_not_buy_local_inference_grace(monkeypatch, provider, read, stale):
    call = _proxy_call()
    call.agent.provider = provider
    monkeypatch.setattr(h, "get_provider_request_timeout", lambda *args: None)
    monkeypatch.setattr(h, "get_provider_stale_timeout", lambda *args: None)
    monkeypatch.setattr(h, "_local_stream_stale_timeout_default", lambda: 900.0)
    for name in ("HERMES_API_TIMEOUT", "HERMES_STREAM_READ_TIMEOUT", "HERMES_STREAM_STALE_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    call._resolve_stale_timeout()
    assert call._stream_stale_timeout == stale
    assert call._stream_timeouts()[1] == read


@pytest.mark.parametrize("owned_stale", [False, True], ids=["socket-read", "owned-stale-abort"])
def test_first_silent_timeout_recovers_once_without_changing_the_frozen_request(monkeypatch, owned_stale):
    call = _proxy_call()
    seen, retries = [], []
    monkeypatch.setattr(call, "_retry_after_drop", lambda *args, **kwargs: retries.append(kwargs))

    def nonstream(agent, payload):
        seen.append(payload)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="recovered", reasoning_content=None))])

    monkeypatch.setattr(h, "interruptible_api_call", nonstream)
    error = _read_timeout()
    if owned_stale:
        error.__cause__ = httpx.ReadError("owned socket shutdown")
        call.stream_attempt_state = {"current": 1}
        call._stale_counted_attempts = {1}
    assert call._handle_stream_error(error, attempt=0, max_retries=2) is False
    assert not retries
    assert len(seen) == 1
    assert seen[0]["messages"] is call.api_kwargs["messages"]
    assert seen[0]["tools"] is call.api_kwargs["tools"]
    assert "stream" not in seen[0]
    assert call.api_kwargs["stream"] is True
    assert call.result["error"] is None
    assert call.deltas == ["recovered"]
    assert call.agent._disable_streaming is True


@pytest.mark.parametrize("fence", [
    lambda call, error: call.result.update(partial_tool_names=["write_once"]),
    lambda call, error: call.clients.diag.update(chunks=1, first_chunk_at=1.0),
    lambda call, error: setattr(call.agent, "_interrupt_requested", True),
    lambda call, error: setattr(call.agent, "model", "other"),
    lambda call, error: setattr(error, "status_code", 401),
    lambda call, error: call._request_cancelled.update(value=True),
], ids=["tool", "reasoning", "cancel", "route", "auth", "cancelled-stale-abort"])
def test_nonstream_timeout_recovery_never_crosses_delivery_or_authority_fences(monkeypatch, fence):
    call = _proxy_call()
    error = _read_timeout()
    call.stream_attempt_state = {"current": 1}
    call._stale_counted_attempts = {1}
    fence(call, error)
    monkeypatch.setattr(h, "interruptible_api_call", lambda *args: pytest.fail("unsafe non-streaming replay"))
    assert call._unmask_server_error_with_nonstreaming(error) is False
    assert call.agent._disable_streaming is False
