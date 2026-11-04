"""Provider-error request diagnostics retain private context only in encrypted owned state."""
from __future__ import annotations

import secrets
from types import SimpleNamespace

import pytest

from agent.session_persistence import SessionPersistenceMixin
from hermes_security import io, vault


@pytest.fixture
def owned_agent(tmp_path, monkeypatch):
    home = tmp_path / "owner"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(home, "synthetic request diagnostic password")
    vault.unlock(home, "synthetic request diagnostic password")
    messages = []
    logs = home / "sessions"
    logs.mkdir()
    agent = SimpleNamespace(logs_dir=logs, api_mode="chat_completions", session_id="synthetic-diagnostic",
                            base_url="http://127.0.0.1:1/v1", client=SimpleNamespace(api_key="synthetic-provider-key"),
                            _mask_api_key_for_logs=lambda _: "[MASKED]", log_prefix="", verbose_logging=True,
                            _vprint=messages.append)
    yield home, agent, messages
    vault.clear_vault_cache()


def _dump(agent, marker):
    return SessionPersistenceMixin._dump_api_request_debug(
        agent, {"model": "synthetic-model", "messages": [{"role": "user", "content": marker}], "timeout": 20},
        reason="non_retryable_client_error", error=RuntimeError("HTTP401 invalid api key"))


def test_provider_error_diagnostic_encrypts_private_prompt_and_preserves_forensics(owned_agent, tmp_path, monkeypatch):
    home, agent, notices = owned_agent
    marker = "synthetic private diagnostic context " + secrets.token_hex(8)
    # The agent's captured logs directory remains its authority even after a thread hop changes ambient home.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "unrelated"))
    target = _dump(agent, marker)
    assert target is not None and target.parent == home / "sessions"
    assert marker.encode() not in target.read_bytes()
    payload = io.read_json(target, purpose="state")
    assert payload["request"]["body"]["messages"][0]["content"] == marker
    assert "timeout" not in payload["request"]["body"]
    assert payload["error"]["message"] == "HTTP401 invalid api key"
    assert "synthetic-provider-key" not in str(payload)
    assert any("encrypted" in notice.lower() for notice in notices)


@pytest.mark.parametrize("failure", ["locked", "missing", "damaged"])
def test_unavailable_request_store_never_falls_back_to_plaintext(owned_agent, monkeypatch, failure, capsys):
    home, agent, notices = owned_agent
    if failure == "locked":
        vault.lock_now(home)
    elif failure == "missing":
        (home / ".hermes-vault").unlink()
    else:
        (home / ".hermes-vault").write_bytes(b"{damaged metadata")
    marker = "synthetic unavailable diagnostic context " + secrets.token_hex(8)
    monkeypatch.setenv("HERMES_DUMP_REQUEST_STDOUT", "1")
    assert _dump(agent, marker) is None
    assert list((home / "sessions").iterdir()) == []
    assert not any("written" in notice.lower() for notice in notices)
    output = capsys.readouterr()
    assert marker not in output.out + output.err


def test_bound_request_store_does_not_adopt_a_later_human_unlock(owned_agent):
    from agent.request_diagnostics import bind_request_diagnostics

    home, agent, notices = owned_agent
    bind_request_diagnostics(agent, home=home)
    original = _dump(agent, "synthetic original diagnostic context")
    assert original is not None
    before = {path.name: path.read_bytes() for path in (home / "sessions").iterdir()}
    vault.lock_now(home)
    vault.unlock(home, "synthetic request diagnostic password")
    assert _dump(agent, "synthetic forbidden renewed diagnostic") is None
    assert {path.name: path.read_bytes() for path in (home / "sessions").iterdir()} == before
    assert "not retained" in notices[-1]


def test_real_agent_diagnostic_reads_anthropic_client_and_messages_url(owned_agent, monkeypatch):
    """Preserve the existing native Anthropic diagnostic's masked key and /messages route."""
    from tests.agent.test_run_agent_codex_responses import _build_agent

    agent = _build_agent(monkeypatch)
    try:
        agent.api_mode = "anthropic_messages"
        agent.base_url = "https://relay.example.com/anthropic"
        agent.client = None
        agent._anthropic_client = SimpleNamespace(api_key="sk-ant-api03-abcdefghijklmnopqrstuvwxyz")
        target = agent._dump_api_request_debug({"model": "claude", "messages": []}, reason="preflight")
        payload = io.read_json(target, purpose="state")
        assert payload["request"]["url"] == "https://relay.example.com/anthropic/messages"
        assert "None" not in payload["request"]["headers"]["Authorization"]
        assert "abcdefghijklmnopqrstuvwxyz" not in payload["request"]["headers"]["Authorization"]
    finally:
        agent.close()


def test_missing_nested_profile_never_uses_an_unlocked_ancestor_vault(owned_agent):
    from agent.request_diagnostics import bind_request_diagnostics

    parent, agent, notices = owned_agent
    selected = parent / "profiles" / "missing-profile"
    bind_request_diagnostics(agent, home=selected)
    assert not (selected / ".hermes-vault").exists()
    assert _dump(agent, "synthetic missing profile diagnostic context") is None
    assert list((selected / "sessions").iterdir()) == []
    assert vault.is_unlocked(parent)
    assert "not retained" in notices[-1]

    # A real independently initialized profile works, but losing its metadata never borrows the parent.
    vault.init_vault(selected, "synthetic independent profile password")
    vault.unlock(selected, "synthetic independent profile password")
    bind_request_diagnostics(agent, home=selected)
    target = _dump(agent, "synthetic own-profile diagnostic context")
    assert target is not None
    assert io.read_json(target, purpose="state")["request"]["body"]["messages"][0]["content"] == (
        "synthetic own-profile diagnostic context")
    before = {path.name: path.read_bytes() for path in (selected / "sessions").iterdir()}
    (selected / ".hermes-vault").unlink()
    assert _dump(agent, "synthetic forbidden ancestor diagnostic context") is None
    assert {path.name: path.read_bytes() for path in (selected / "sessions").iterdir()} == before
    assert vault.is_unlocked(parent)


@pytest.mark.parametrize("consumer", ["scanner", "offline-export"])
def test_diagnostic_and_conventional_transcript_retain_their_distinct_purposes(owned_agent, tmp_path, consumer):
    import json
    import tarfile

    from hermes_security.migrate import scan_for_plaintext
    from hermes_security.recovery_export import export_home

    home, agent, _ = owned_agent
    marker = "synthetic private forensic context " + secrets.token_hex(8)
    target = _dump(agent, marker)
    assert target is not None
    conventional = home / "sessions" / "conversation.json"
    io.write_json(conventional, {"ordinary": "preserved conversation"}, purpose="transcript")
    before = {path.name: path.read_bytes() for path in (home / "sessions").iterdir()}
    if consumer == "scanner":
        assert scan_for_plaintext(home) == []
    else:
        archive = tmp_path / "explicit-forensic-export.tar"
        export_home(home, archive)
        with tarfile.open(archive) as saved:
            diagnostic = json.load(saved.extractfile(target.relative_to(home).as_posix()))
            transcript = json.load(saved.extractfile("sessions/conversation.json"))
        assert diagnostic["request"]["body"]["messages"][0]["content"] == marker
        assert transcript == {"ordinary": "preserved conversation"}
    assert {path.name: path.read_bytes() for path in (home / "sessions").iterdir()} == before
