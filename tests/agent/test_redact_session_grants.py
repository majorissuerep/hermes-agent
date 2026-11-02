"""A nested executor's grant stays private even outside its minting process."""
import json
import secrets

import pytest

from agent import redact


@pytest.mark.parametrize("encoding", ["json", "environment", "executor-json"])
def test_nested_authority_tokens_are_redacted_without_a_local_registration(monkeypatch, encoding):
    monkeypatch.setattr(redact, "_redact_enabled", lambda: False)
    token = secrets.token_urlsafe(32)
    grant = json.dumps({"token": token, "pipe": "/tmp/synthetic-kernel-pipes",
                        "home": "/tmp/synthetic-owner", "session_id": "managed-synthetic"})
    rendered = {"json": grant, "environment": "HERMES_SESSION_VAULT=" + grant,
                "executor-json": json.dumps({"aggregated_output": grant})}[encoding]
    for output in (redact.redact_terminal_output(rendered, "hermes manage run codex inspect"),
                   redact.redact_sensitive_text(rendered)):
        assert token not in output
        assert "managed-synthetic" in output and "/tmp/synthetic-owner" in output


def test_ordinary_application_tokens_are_not_treated_as_hermes_unlock_grants(monkeypatch):
    monkeypatch.setattr(redact, "_redact_enabled", lambda: False)
    text = json.dumps({"token": "ordinary-application-data", "session_id": "example"})
    assert redact.redact_sensitive_text(text) == text
