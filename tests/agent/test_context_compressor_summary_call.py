"""Compaction summaries survive a thinking summarizer that exhausts its relay's output cap.

Regression: a LiteLLM gateway fronting Qwen3.8 applies the model's ``max_completion_tokens`` (12,288)
when a request omits ``max_tokens``, which the summary call always does. With thinking at the server
default the model spent the whole cap on ``reasoning_content`` and stopped with ``finish_reason=length``
and empty content. Every compaction failed as a truncated summary, walked the 60/300/900 s cooldown
ladder, and the transcript grew until the main request overflowed the context window.

The relay below reproduces that wire behaviour and the requests go through the real auxiliary client,
so these assertions are on what actually reaches the endpoint.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.context_compressor import ContextCompressor
from agent.context_compressor_summary_call import summary_reasoning_exhausted

_MODEL = "relay/qwen3.8-flash"
_CAP = 12288
_SUMMARY = "## Goal\nShip the parser fix.\n\n## Completed Actions\n1. Read parser.py.\n"


class _ThinkingRelay:
    """OpenAI-compatible relay whose model thinks past the output cap unless the request turns thinking
    off with ``reasoning_effort: "none"`` (``honors_reasoning_off=False`` emulates a relay that drops it)."""

    def __init__(self, *, honors_reasoning_off: bool = True):
        self.honors_reasoning_off = honors_reasoning_off
        self.requests: list[dict] = []
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, payload: dict) -> None:
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._json({"object": "list", "data": [
                    {"id": _MODEL, "context_length": 262144, "max_completion_tokens": _CAP}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
                relay.requests.append(body)
                thinking = not (relay.honors_reasoning_off and body.get("reasoning_effort") == "none")
                message = {"role": "assistant", "content": "" if thinking else _SUMMARY}
                if thinking:
                    message["reasoning_content"] = "Let me weigh every turn carefully. " * 1500
                finish = "length" if thinking else "stop"
                if not body.get("stream"):
                    self._json({"id": "c1", "object": "chat.completion", "model": _MODEL,
                                "choices": [{"index": 0, "message": message, "finish_reason": finish}]})
                    return
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                delta = {k: v for k, v in message.items() if v}
                for chunk in (
                    {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
                ):
                    payload = {"id": "c1", "object": "chat.completion.chunk", "model": _MODEL, **chunk}
                    self.wfile.write(b"data: " + json.dumps(payload).encode() + b"\n\n")
                self.wfile.write(b"data: [DONE]\n\n")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> _ThinkingRelay:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=10)


def _compressor(base_url: str) -> ContextCompressor:
    return ContextCompressor(
        model=_MODEL, provider="custom", base_url=base_url, api_key="sk-test", api_mode="chat_completions",
        quiet_mode=True, protect_first_n=3, protect_last_n=6, config_context_length=262144,
    )


def _transcript(tag: str) -> list[dict]:
    messages = [{"role": "system", "content": "you are hermes"}]
    for i in range(30):
        messages.append({"role": "user", "content": f"{tag} user {i}: inspect file_{i}.py " + "x" * 300})
        messages.append({"role": "assistant", "content": f"{tag} assistant {i}: inspected file_{i}.py " + "y" * 300})
    return messages


def _summary_committed(messages: list[dict]) -> bool:
    return any("Ship the parser fix." in str(m.get("content")) for m in messages)


def test_reasoning_exhausted_summary_is_reissued_once_with_reasoning_off():
    with _ThinkingRelay() as relay:
        compressor = _compressor(relay.base_url)
        first = _transcript("a")
        compacted = compressor.compress(first, current_tokens=200000, force=True)

        assert len(compacted) < len(first) and _summary_committed(compacted)
        assert compressor._last_summary_truncated_failure is False
        # The first request keeps the historic shape: no output cap, no reasoning override.
        assert [r.get("reasoning_effort") for r in relay.requests] == [None, "none"]
        assert not any("max_tokens" in r or "max_completion_tokens" in r for r in relay.requests)

        # The proof is remembered for the route: a later compaction does not re-burn the cap first.
        second = _transcript("b")
        assert _summary_committed(compressor.compress(second, current_tokens=200000, force=True))
        assert [r.get("reasoning_effort") for r in relay.requests] == [None, "none", "none"]


def test_relay_that_ignores_reasoning_off_fails_bounded_and_keeps_the_transcript():
    with _ThinkingRelay(honors_reasoning_off=False) as relay:
        compressor = _compressor(relay.base_url)
        messages = _transcript("a")

        assert compressor.compress(messages, current_tokens=200000, force=True) == messages
        assert len(relay.requests) == 2
        assert compressor._last_summary_truncated_failure is True
        assert compressor._previous_summary is None
        assert "auxiliary.compression.extra_body" in (compressor._last_summary_error or "")


def test_configured_thinking_off_is_not_reissued_identically():
    config = {"provider": "auto", "reasoning_effort": "none"}
    with (
        _ThinkingRelay(honors_reasoning_off=False) as relay,
        patch("agent.auxiliary_client._get_auxiliary_task_config", return_value=config),
    ):
        compressor = _compressor(relay.base_url)
        messages = _transcript("a")

        assert compressor.compress(messages, current_tokens=200000, force=True) == messages
        assert [r.get("reasoning_effort") for r in relay.requests] == ["none"]


def _response(finish_reason: str, **message) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(**message), finish_reason=finish_reason)])


@pytest.mark.parametrize(
    ("response", "exhausted"),
    [
        (_response("length", content="", reasoning_content="thinking..."), True),
        (_response("length", content="", reasoning="thinking..."), True),
        (_response("length", content="<think>still weighing the turns"), True),
        (_response("length", content="## Goal\nA summary that genuinely ran lo"), False),
        (_response("stop", content="", reasoning_content="thinking..."), False),
    ],
)
def test_only_a_length_stop_that_carried_reasoning_counts_as_exhaustion(response, exhausted):
    assert summary_reasoning_exhausted(response) is exhausted
