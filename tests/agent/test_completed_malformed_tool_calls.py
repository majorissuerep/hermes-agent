"""Completed malformed calls must not masquerade as a retryable output-cap event."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.chat_completion_helpers import _StreamingCall
from agent.transports.chat_completions import ChatCompletionsTransport
from agent.turn_tool_validation import validate_tool_calls


def _finish(reason):
    call = _StreamingCall.__new__(_StreamingCall)
    call.agent = SimpleNamespace()
    broken = '{"path": "out.md", "content": "# Title\\n\\nsays "quoted" and then, '
    accumulated = {0: {"id": "write-1", "type": "function", "function": {
        "name": "write_file", "arguments": broken}}}
    response = call._finish_chat_stream(
        None, "assistant", [], [], accumulated, reason, "test-model", None,
        flush_pending=lambda: None)
    return response, broken


@pytest.mark.parametrize("reason", ["stop", "tool_calls"])
def test_completed_malformed_call_fails_visibly_instead_of_silent_replay(reason):
    response, broken = _finish(reason)
    message = ChatCompletionsTransport().normalize_response(response)
    assert message.finish_reason == reason
    assert message.tool_calls[0].arguments == broken
    agent = Mock(valid_tool_names={"write_file"}, log_prefix="", _invalid_tool_retries=0)
    verdict = validate_tool_calls(
        agent, message, reason, messages=[{"role": "user", "content": "write a file"}],
        conversation_history=[], api_call_count=1, effective_task_id="task-1")
    assert verdict.action == "return"
    assert verdict.result["partial"] is True and verdict.result["completed"] is False
    assert verdict.result["error"]
    agent._persist_session.assert_called_once()


@pytest.mark.parametrize("reason", [None, "length"])
def test_real_dropped_or_output_capped_call_keeps_recovery_signal(reason):
    response, _ = _finish(reason)
    assert response.choices[0].finish_reason == "length"
    assert bool(response.choices[0].message.tool_calls) is (reason == "length")
