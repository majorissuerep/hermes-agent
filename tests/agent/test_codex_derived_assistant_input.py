"""Derived/compacted assistant messages must remain valid Responses input."""
from __future__ import annotations

import pytest

from agent.codex_responses_adapter import _chat_messages_to_responses_input
from tests.fakes.providers.openai_responses import validate_request


@pytest.mark.parametrize("issuer", ["codex_backend", "openai_api"])
@pytest.mark.parametrize("content", ["retained derived answer", [{"type": "text", "text": "retained summary"}]])
@pytest.mark.parametrize("replayed", [False, True])
def test_derived_assistant_text_validates_against_responses_request_schema(issuer, content, replayed):
    assistant = {"role": "assistant", "content": content}
    if replayed:
        assistant["codex_message_items"] = [{"type": "message", "role": "assistant", "id": "msg_retained",
                                             "status": "completed", "content": [
                                                 {"type": "output_text", "text": "retained answer"}]}]
    items = _chat_messages_to_responses_input(
        [{"role": "user", "content": "continue"}, assistant],
        current_issuer_kind=issuer,
    )
    assert validate_request({"model": "schema-probe", "input": items, "store": False, "stream": True}) is None
