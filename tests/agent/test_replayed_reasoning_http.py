"""Reported replay costs survive the real encrypted headless CLI and process resume."""
import json

import pytest

from agent.context_compressor import _estimate_msg_budget_tokens
from tests.e2e.core.providers._openai_helpers import (
    Home, READ_TOOL, db_messages, oneshot, responses_provider_config, responses_input_items,
)
from tests.fakes.providers.openai_responses import FakeResponsesServer, FunctionCall, Message, Reasoning, Turn


@pytest.mark.platforms("posix")
def test_reported_reasoning_survives_encrypted_cli_resume_without_leaking_accounting_fields(tmp_path):
    home = Home(tmp_path)
    script = [
        Turn([Reasoning("x"), FunctionCall(READ_TOOL, {"path": "notes.txt"})],
             output_tokens=8_005, reasoning_tokens=8_000),
        Turn([Reasoning("y"), Message("FIRST-ANSWER")], output_tokens=78, reasoning_tokens=73),
        Turn([Message("RESUMED-ANSWER")]),
    ]
    with FakeResponsesServer(script) as server:
        home.write(responses_provider_config(server.base_url))
        (home.project / "notes.txt").write_text("REPLAY-CONTINUITY\n", encoding="utf-8")
        first = oneshot(home, "Read notes.txt and answer")
        assert first.proc.returncode == 0 and first.stdout.strip() == "FIRST-ANSWER", first.describe()
        resumed = oneshot(home, "Continue the same conversation", resume=first.session_id)
        assert resumed.proc.returncode == 0 and resumed.stdout.strip() == "RESUMED-ANSWER", resumed.describe()
        requests = server.main_requests()
        assert server.invalid_requests() == []

    assert len(requests) == len(script)
    output = responses_input_items(requests[1], "function_call_output")
    assert len(output) == 1 and "REPLAY-CONTINUITY" in output[0]["output"]
    assert [item["encrypted_content"] for item in responses_input_items(requests[2], "reasoning")] == ["x", "y"]
    for request in requests:
        assert "_replayed_reasoning_tokens" not in json.dumps(request)
        assert "display_metadata" not in json.dumps(request)

    rows = [row for row in db_messages(home, first.session_id) if row["role"] == "assistant"]
    counts = [json.loads(row["display_metadata"] or "{}").get("reasoning_tokens", 0) for row in rows]
    assert counts == [8_000, 73, 0]
    for row, count in zip(rows, counts):
        row["display_metadata"] = json.loads(row["display_metadata"] or "{}")
        row["codex_reasoning_items"] = json.loads(row["codex_reasoning_items"] or "[]")
        assert _estimate_msg_budget_tokens(row) >= count
    assert not home.db_path.read_bytes().startswith(b"SQLite format 3")
