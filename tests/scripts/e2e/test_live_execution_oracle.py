"""Durable live-proof results tolerate native notes but never borrow a sibling's tool reply."""

import io
import json
import sys

import pytest


@pytest.fixture(autouse=True)
def _restore_probe_scope():
    from agent.secret_scope import (
        reset_multiplex_context, reset_secret_scope, set_multiplex_context, set_secret_scope,
    )
    from hermes_security import vault

    scope = set_secret_scope(None)
    multiplex = set_multiplex_context(False)
    try:
        yield
    finally:
        reset_multiplex_context(multiplex)
        reset_secret_scope(scope)
        vault.clear_vault_cache()


@pytest.mark.parametrize("child_result", ["noted-success", "missing"])
def test_live_execution_proof_uses_its_own_successful_terminal_result(
    tmp_path, monkeypatch, capsys, child_result,
):
    from hermes_security import vault
    from hermes_cli.config import save_config
    from hermes_state import SessionDB
    from scripts.e2e import state_probe

    home = tmp_path / "state"
    monkeypatch.setenv("HERMES_HOME", str(home))
    password = "synthetic-live-proof-password"
    vault.init_vault(home, password)
    vault.unlock(home, password)
    save_config({"agent": {"max_turns": 23}}, merge_existing=False)

    def call(identity, name, command):
        return {"id": identity, "type": "function", "function": {
            "name": name, "arguments": json.dumps({"command": command}),
        }}

    with SessionDB() as db:
        db.create_session("parent", "cli")
        db.create_session("child", "cli", parent_session_id="parent")
        db.append_message("parent", "assistant", tool_calls=[
            call("delegate", "delegate_task", "test child"),
            call("shared", "terminal", "hermes update --check"),
            call("set", "terminal", "hermes config set agent.max_turns 23"),
        ])
        for identity in ("shared", "set"):
            db.append_message("parent", "tool", json.dumps({"exit_code": 0, "stdout": "done"}),
                              tool_name="terminal", tool_call_id=identity)
        db.append_message("child", "assistant", tool_calls=[
            call("shared", "terminal", "hermes config get agent.max_turns"),
        ])
        if child_result == "noted-success":
            db.append_message("child", "tool", json.dumps({"exit_code": 0, "stdout": "17"})
                              + "\n\n[System note: command completed; no further polling is needed.]",
                              tool_name="terminal", tool_call_id="shared")

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "action": "live-proof", "password": password, "allow_pending": True,
    })))
    state_probe.main()
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert result["complete"] is (child_result == "noted-success")
    assert ("hermes config get agent.max_turns" in result["executed_in_child"]) is (
        child_result == "noted-success"
    )
