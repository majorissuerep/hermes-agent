"""Diagnostics for Codex Responses transport failures."""

from __future__ import annotations

import json
import logging
import re
import secrets
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError

from agent.codex_runtime import _codex_request_failure_details, run_codex_stream


@pytest.fixture
def sealed_codex_agent(tmp_path, monkeypatch):
    from hermes_security import vault
    from tests.agent.test_run_agent_codex_responses import _build_agent

    home = tmp_path / "owning-profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(home, "synthetic transport archive password")
    vault.unlock(home, "synthetic transport archive password")
    agent = _build_agent(monkeypatch)
    try:
        assert Path(agent.logs_dir) == home / "sessions"
        yield home, agent
    finally:
        agent.close()
        vault.clear_vault_cache()


def test_transport_failure_without_attached_request_reports_unknown_size():
    error = httpx.RemoteProtocolError("connection closed")

    request_body_bytes, exception_chain = _codex_request_failure_details(error)

    assert request_body_bytes is None
    assert exception_chain == "RemoteProtocolError"


def test_transport_failure_logs_exact_request_bytes_and_class_chain(caplog):
    request_content = b'{"input":"payload"}'
    request = httpx.Request(
        "POST",
        "https://example.invalid/responses",
        content=request_content,
    )
    transport_error = httpx.RemoteProtocolError(
        "server disconnected without sending a response",
        request=request,
    )
    connection_error = APIConnectionError(request=request)
    connection_error.__cause__ = transport_error

    class FailingResponses:
        def create(self, **_kwargs):
            raise connection_error

    client = SimpleNamespace(responses=FailingResponses())
    agent = SimpleNamespace(
        _interrupt_requested=False,
        _current_api_request_id="request-id",
        _fallback_index=0,
        is_subagent=False,
        model="gpt-5.6-sol",
        provider="openai-codex",
        session_id="",
        _client_log_context=lambda: "",
        _buffer_diagnostic_status=lambda message: None,
    )

    with caplog.at_level(logging.WARNING, logger="agent.codex_runtime"):
        with pytest.raises(APIConnectionError):
            run_codex_stream(agent, {"model": "gpt-5.6-sol"}, client=client)

    message = caplog.messages[-1]
    assert f"serialized_request_body_bytes={len(request_content)}" in message
    assert "stream_opened=false" in message
    assert "exception_chain=APIConnectionError <- RemoteProtocolError" in message
    assert "attempt=2/2" in message
    assert "payload" not in message
    assert request_content.decode() not in message
    assert "example.invalid" not in message


def _zero_event_then_completed_client(seen_inputs: list):
    """``responses.create`` that dies before any stream event on the first call and completes on
    the second; records the ``input`` list each physical attempt was given."""
    from tests.agent.test_run_agent_codex_responses import _FakeCreateStream

    events = [
        SimpleNamespace(type="response.output_item.done", item=SimpleNamespace(
            type="message", status="completed", content=[SimpleNamespace(type="output_text", text="ok")])),
        SimpleNamespace(type="response.completed", response=SimpleNamespace(
            status="completed", usage=SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2), id="r1")),
    ]

    def _create(**kwargs):
        seen_inputs.append(kwargs.get("input") or (kwargs.get("extra_body") or {}).get("input"))
        if len(seen_inputs) == 1:
            raise httpx.ConnectError("no first byte")
        return _FakeCreateStream(events)

    return SimpleNamespace(responses=SimpleNamespace(create=_create))


def _oversized_codex_kwargs(size: int) -> dict:
    return {"model": "gpt-5-codex", "instructions": "You are Hermes.", "store": False, "tools": None,
            "input": [{"role": "user", "content": "look"},
                      {"type": "function_call", "call_id": "call_browser", "name": "browser_exec", "arguments": "{}"},
                      {"type": "function_call_output", "call_id": "call_browser", "output": "x" * size},
                      {"role": "user", "content": "continue"}]}


def test_zero_event_retry_prunes_oversized_tool_output_and_logs_size_delta(sealed_codex_agent, caplog):
    """#95429 criterion 3: a reconnect after a zero-event attempt must not resend the same oversized
    payload unchanged -- the inline tool output is spilled and the size delta is logged."""
    from hermes_security import artifacts
    from tools.tool_result_storage import PERSISTED_OUTPUT_TAG, extract_persisted_path

    home, agent = sealed_codex_agent
    seen: list = []
    agent.client = _zero_event_then_completed_client(seen)
    canary = "synthetic private retry output " + secrets.token_hex(16)
    original_output = canary + "x" * (760_396 - len(canary))
    kwargs = _oversized_codex_kwargs(len(original_output))
    kwargs["input"][2]["output"] = original_output

    with caplog.at_level(logging.INFO, logger="agent.codex_runtime"):
        response = agent._run_codex_stream(kwargs)

    assert response.status == "completed" and len(seen) == 2
    first, second = (len(json.dumps(i)) for i in seen)
    assert second < first // 10
    retried_output = seen[1][2]["output"]
    assert PERSISTED_OUTPUT_TAG in retried_output and len(retried_output) < 10_000
    persisted_path = Path(extract_persisted_path(retried_output))
    assert persisted_path == home / "artifacts" / "tool-results" / "call_browser.txt"
    ciphertext = persisted_path.read_bytes()
    assert ciphertext.startswith(b"HRMVAULT\x00") and canary.encode() not in ciphertext
    assert artifacts.read_text(persisted_path, home=home) == original_output
    assert kwargs["input"][2]["output"] == seen[0][2]["output"] == original_output  # caller is not mutated
    prune_logs = [r.message for r in caplog.records if "zero-event" in r.message]
    assert len(prune_logs) == 1 and "attempt 1/2" in prune_logs[0]
    assert re.search(r"serialized_input_bytes=\d+ -> \d+", prune_logs[0])


def test_zero_event_retry_without_prunable_output_logs_unchanged_resend(monkeypatch, caplog):
    from tests.agent.test_run_agent_codex_responses import _build_agent

    agent = _build_agent(monkeypatch)
    seen: list = []
    agent.client = _zero_event_then_completed_client(seen)
    kwargs = _oversized_codex_kwargs(300_000)
    kwargs["input"][2]["output"] = ["not-a-string"]  # nothing prunable
    kwargs["input"][0]["content"] = "u" * 300_000  # still a large payload

    with caplog.at_level(logging.INFO, logger="agent.codex_runtime"):
        response = agent._run_codex_stream(kwargs)

    assert response.status == "completed" and seen[0] == seen[1]
    (log,) = [r.message for r in caplog.records if "zero-event" in r.message]
    assert "unchanged" in log and "attempt 1/2" in log and re.search(r"serialized_input_bytes=\d+", log)
