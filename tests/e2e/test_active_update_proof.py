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


def _delegation():
    return [{"id": 7, "session_id": "parent", "role": "assistant", "tool_call_id": None,
             "content": None, "tool_calls": json.dumps([{"id": "delegated", "function": {
                 "name": "delegate_task", "arguments": "{}"}}])},
            {"id": 8, "session_id": "parent", "role": "tool", "tool_call_id": "delegated",
             "tool_calls": None, "content": json.dumps(_dispatch())}]


def test_successful_update_requires_later_parent_and_child_native_calls():
    rows = (_call(1, "parent", UPDATE_COMMAND) + _call(3, "parent", SET_COMMAND) +
            _call(5, "child", GET_COMMAND))
    rows += _delegation()
    proof = verify_calls(rows, "parent", {"child"}, delegations=_receipts(), child_links=_links())
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


@pytest.mark.parametrize("result", [None, {"results": []}, {"results": [{"status": "failed"}]},
                                    {"results": [{"status": "completed"}, {"status": "failed"}]},
                                    {"status": "completed", "results": [{"summary": "23"}]}])
def test_child_rows_without_completed_parent_delegate_tool_do_not_qualify(result):
    rows = (_call(1, "parent", UPDATE_COMMAND) + _call(3, "parent", SET_COMMAND) +
            _call(5, "child", GET_COMMAND))
    if result is not None:
        rows += _delegation()
        rows[-1]["content"] = json.dumps(result)
    with pytest.raises(RuntimeError, match="actual delegate_task"):
        verify_calls(rows, "parent", {"child"})


def _tool(number, session, name, args, result):
    identity = f"native-{number}"
    return [{"id": number, "session_id": session, "role": "assistant", "tool_call_id": None,
             "tool_calls": json.dumps([{"id": identity, "function": {"name": name, "arguments": json.dumps(args)}}])},
            {"id": number + 1, "session_id": session, "role": "tool", "tool_call_id": identity,
             "tool_calls": None, "content": json.dumps(result)}]


def _async_delegation_case():
    rows = (_call(1, "parent", UPDATE_COMMAND) + _call(3, "parent", SET_COMMAND) + _call(5, "child", GET_COMMAND))
    rows += _tool(7, "parent", "delegate_task", {"goal": "read config"},
                  {"status": "dispatched", "mode": "background", "delegation_id": "delegation-owned", "subagent_ids": ["sa-owned"]})
    receipts = [{"delegation_id": "delegation-owned", "parent_session_id": "parent", "state": "completed",
                 "completed_at": 123.5, "result_json": json.dumps({"results": [{"task_index": 0, "status": "completed"}]})}]
    return rows, receipts


def test_native_async_delegation_requires_joined_durable_completion():
    rows, receipts = _async_delegation_case()
    assert verify_calls(rows, "parent", {"child"}, delegations=receipts, child_links=_links())["completed_parent_delegations"] == 1


@pytest.mark.parametrize("field,value", [
    ("delegation_id", "some-other-delegation"), ("parent_session_id", "another-owner"),
    ("state", "running"), ("completed_at", None),
    ("result_json", '{"results": []}'), ("result_json", '{"results": [{"status": "failed"}]}'),
])
def test_dispatch_words_or_foreign_unfinished_ledger_cannot_qualify(field, value):
    rows, receipts = _async_delegation_case()
    receipts[0][field] = value
    with pytest.raises(RuntimeError, match="actual delegate_task"):
        verify_calls(rows, "parent", {"child"}, delegations=receipts, child_links=_links())


def _background_update_case():
    rows = _tool(1, "parent", "terminal", {"command": UPDATE_COMMAND},
                 {"session_id": "proc_owned", "exit_code": 0, "output": "Background process started"})
    rows += _tool(3, "parent", "process_manage", {"action": "wait", "session_id": "proc_owned"},
                  {"status": "exited", "command": UPDATE_COMMAND, "exit_code": 0})
    rows += _call(5, "parent", SET_COMMAND) + _call(7, "child", GET_COMMAND)
    rows += _tool(9, "parent", "delegate_task", {}, _dispatch())
    processes = [{"id": "proc_owned", "command": UPDATE_COMMAND, "parent_session_id": "parent", "exit_code": 0}]
    return rows, processes


def test_background_spawn_requires_explicit_mode_and_same_owned_completed_wait():
    rows, processes = _background_update_case()
    with pytest.raises(RuntimeError, match="foreground update required"):
        verify_calls(rows, "parent", {"child"}, processes=processes)
    proof = verify_calls(rows, "parent", {"child"}, processes=processes, allow_background_update=True,
                     delegations=_receipts(), child_links=_links())
    assert proof["update_mode"] == "background-joined"
    assert proof["update_message_id"] == 4


def _unrelated_wait(rows, processes):
    calls = json.loads(rows[2]["tool_calls"])
    calls[0]["function"]["arguments"] = json.dumps({"action": "wait", "session_id": "proc_unrelated"})
    rows[2]["tool_calls"] = json.dumps(calls)


@pytest.mark.parametrize("mutate", [
    pytest.param(_unrelated_wait, id="wrong-wait-id"),
    pytest.param(lambda rows, processes: rows[3].update(content=json.dumps(
        {"status": "running", "command": UPDATE_COMMAND, "exit_code": 0})), id="fake-wait"),
    pytest.param(lambda rows, processes: processes[0].update(id="proc_unrelated"), id="wrong-receipt-id"),
    pytest.param(lambda rows, processes: processes[0].update(parent_session_id="other"), id="foreign-owner"),
    pytest.param(lambda rows, processes: processes[0].update(exit_code=2), id="failed"),
    pytest.param(lambda rows, processes: processes.clear(), id="missing"),
])
def test_any_zero_exit_or_unjoined_process_cannot_qualify_background_update(mutate):
    rows, processes = _background_update_case()
    mutate(rows, processes)
    with pytest.raises(RuntimeError, match="same owned process"):
        verify_calls(rows, "parent", {"child"}, processes=processes, allow_background_update=True,
                     delegations=_receipts(), child_links=_links())


def _links():
    return [{"parent_session_id": "parent", "child_session_id": "child", "child_subagent_id": "sa-owned"}]


@pytest.mark.parametrize("defect", ["wrong-dispatch", "foreign-child", "missing-hook", "wrong-index"])
def test_completed_unrelated_children_cannot_qualify(defect):
    rows, receipts = _async_delegation_case()
    links = _links()
    if defect == "wrong-dispatch":
        value = json.loads(rows[-1]["content"])
        value["subagent_ids"] = ["sa-foreign"]
        rows[-1]["content"] = json.dumps(value)
    elif defect == "foreign-child":
        links[0]["child_session_id"] = "unrelated-child"
    elif defect == "missing-hook":
        links = []
    else:
        receipts[0]["result_json"] = json.dumps({"results": [{"task_index": 9, "status": "completed"}]})
    with pytest.raises(RuntimeError, match="actual delegate_task"):
        verify_calls(rows, "parent", {"child"}, delegations=receipts, child_links=links)


def _dispatch():
    return {"status": "dispatched", "mode": "background", "delegation_id": "delegation-owned",
            "subagent_ids": ["sa-owned"]}


def _receipts():
    return [{"delegation_id": "delegation-owned", "parent_session_id": "parent", "state": "completed",
             "completed_at": 123.5,
             "result_json": json.dumps({"results": [{"task_index": 0, "status": "completed"}]})}]


def test_inline_completion_cannot_bypass_native_dispatch_identity_proof():
    rows, receipts = _async_delegation_case()
    rows[-1]["content"] = json.dumps({"results": [{"task_index": 0, "status": "completed"}]})
    with pytest.raises(RuntimeError, match="actual delegate_task"):
        verify_calls(rows, "parent", {"child"}, delegations=receipts, child_links=_links())
