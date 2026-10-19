"""The active-session receipt must not accept a check-only, failed or wrong-owner child."""
import json

import pytest

from scripts.e2e.active_update_proof import GET_COMMAND, SET_COMMAND, UPDATE_COMMAND, verify_calls


def _call(number, session, command, exit_code=0):
    identity = f"call-{number}"
    return [{"id": number, "session_id": session, "role": "assistant", "tool_call_id": None,
             "content": None, "tool_calls": json.dumps([{"id": identity, "function": {
                 "name": "terminal", "arguments": json.dumps({"command": command})}}])},
            {"id": number + 1, "session_id": session, "role": "tool", "tool_call_id": identity,
             "tool_calls": None, "content": json.dumps({"exit_code": exit_code, "output": "23" if command == GET_COMMAND else "success"}) +
             "\n[Terminal runtime note: the command has finished.]"}]


def test_successful_update_requires_later_parent_and_child_native_calls():
    rows = (_call(1, "parent", UPDATE_COMMAND) + _call(3, "parent", SET_COMMAND) +
            _call(5, "child", GET_COMMAND))
    proof = verify_calls(rows, "parent", {"child"})
    assert proof["successful_update_calls"] == 1
    assert proof["post_update_parent_config_writes"] == proof["post_update_child_config_reads"] == 1


@pytest.mark.parametrize("command,exit_code", [("hermes update --check", 0), (UPDATE_COMMAND, 2), (UPDATE_COMMAND, False)])
def test_success_words_do_not_qualify_check_only_or_failed_update(command, exit_code):
    rows = (_call(1, "parent", command, exit_code) + _call(3, "parent", SET_COMMAND) +
            _call(5, "child", GET_COMMAND))
    with pytest.raises(RuntimeError, match="exactly one successful real update"):
        verify_calls(rows, "parent", {"child"})


@pytest.mark.parametrize("owner,first_id", [("parent", 5), ("child", -1)])
def test_parent_only_or_preupdate_delegation_does_not_qualify(owner, first_id):
    rows = (_call(1, "parent", UPDATE_COMMAND) + _call(3, "parent", SET_COMMAND) +
            _call(first_id, owner, GET_COMMAND))
    with pytest.raises(RuntimeError, match="post-update"):
        verify_calls(rows, "parent", {"child"})


def test_child_reading_the_wrong_profile_value_does_not_qualify():
    rows = (_call(1, "parent", UPDATE_COMMAND) + _call(3, "parent", SET_COMMAND) +
            _call(5, "child", GET_COMMAND))
    rows[-1]["content"] = json.dumps({"exit_code": 0, "output": "37"})
    with pytest.raises(RuntimeError, match="post-update"):
        verify_calls(rows, "parent", {"child"})
