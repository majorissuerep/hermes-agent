"""Strict native tool/ledger joins for an already-unlocked terminal update."""
from __future__ import annotations

import hashlib
import json

UPDATE_COMMAND = "hermes update --yes --keep-stash --backup --branch main"
GET_COMMAND = "hermes config get agent.max_turns"
SET_COMMAND = "hermes config set agent.max_turns 23"


def prefix_hash(messages: list[dict]) -> str:
    return hashlib.sha256(json.dumps(messages, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def paired_calls(messages: list[dict]) -> list[dict]:
    replies = {(row["session_id"], row["tool_call_id"]): row for row in messages if row["role"] == "tool"}
    pairs = []
    for row in messages:
        for call in json.loads(row.get("tool_calls") or "[]"):
            function = call["function"]
            reply = replies.get((row["session_id"], call["id"]))
            if function["name"] not in {"terminal", "process_manage", "delegate_task"} or reply is None:
                continue
            result, _ = json.JSONDecoder().raw_decode(reply["content"].lstrip())
            if not isinstance(result, dict):
                raise RuntimeError("native tool completion must be a structured object")
            pairs.append({"session_id": row["session_id"], "message_id": reply["id"],
                          "name": function["name"], "args": json.loads(function["arguments"]), "result": result})
    return pairs


def terminal_calls(messages: list[dict]) -> list[dict]:
    return [{"session_id": pair["session_id"], "message_id": pair["message_id"],
             "command": pair["args"].get("command", "").strip(),
             "process_id": pair["result"].get("session_id"),
             "exit_code": pair["result"].get("exit_code") if type(pair["result"].get("exit_code")) is int else None,
             "output": str(pair["result"].get("output", ""))}
            for pair in paired_calls(messages) if pair["name"] == "terminal"]


def _completed_results(result: dict) -> bool:
    entries = result.get("results")
    return bool(isinstance(entries, list) and entries and all(
        isinstance(entry, dict) and entry.get("status") == "completed" for entry in entries))


def _joined_children(dispatch, result, parent_id, children, links):
    identities = dispatch.get("subagent_ids")
    entries = result.get("results")
    if not _completed_results(result) or not isinstance(identities, list) or not identities:
        return False
    if len(set(identities)) != len(identities) or len(entries) != len(identities):
        return False
    indexes = [entry.get("task_index") for entry in entries]
    if any(type(index) is not int for index in indexes) or set(indexes) != set(range(len(identities))):
        return False
    for index in indexes:
        matching = [link for link in links if link.get("child_subagent_id") == identities[index]
                    and link.get("parent_session_id") == parent_id]
        if len(matching) != 1 or matching[0].get("child_session_id") not in children:
            return False
    return True


def completed_delegations(messages: list[dict], parent_id: str, after: int, receipts: list[dict], children: set[str], links: list[dict]) -> int:
    completed = 0
    for pair in paired_calls(messages):
        if pair["name"] != "delegate_task" or pair["session_id"] != parent_id or pair["message_id"] <= after:
            continue
        result = pair["result"]
        if result.get("status") == "dispatched" and result.get("mode") == "background":
            completed += any(receipt["delegation_id"] == result.get("delegation_id")
                and receipt["parent_session_id"] == parent_id and receipt["state"] == "completed"
                and isinstance(receipt.get("completed_at"), (int, float)) and receipt["completed_at"] > 0
                and _joined_children(result, json.loads(receipt["result_json"]), parent_id, children, links)
                for receipt in receipts)
    return completed


def _update_completion(update: dict, messages: list[dict], parent_id: str,
                       processes: list[dict], allow_background: bool) -> tuple[int, str]:
    process_id = update.get("process_id")
    if not process_id:
        if update["exit_code"] != 0:
            raise RuntimeError("the same parent must complete exactly one successful real update child")
        return update["message_id"], "foreground"
    # Spawn's exit_code=0 acknowledges dispatch, not command completion.
    if not allow_background:
        raise RuntimeError("foreground update required; a tracked spawn acknowledgement is not completion")
    owned = any(record.get("id") == process_id and record.get("parent_session_id") == parent_id
                and record.get("command") == UPDATE_COMMAND and type(record.get("exit_code")) is int
                and record["exit_code"] == 0 for record in processes)
    waits = [pair for pair in paired_calls(messages)
        if pair["name"] == "process_manage" and pair["session_id"] == parent_id
        and pair["message_id"] > update["message_id"] and pair["args"].get("action") == "wait"
        and pair["args"].get("session_id") == process_id and pair["result"].get("status") == "exited"
        and pair["result"].get("command") == UPDATE_COMMAND
        and type(pair["result"].get("exit_code")) is int and pair["result"]["exit_code"] == 0]
    if not owned or not waits:
        raise RuntimeError("background update requires the same owned process receipt and paired successful wait")
    return min(pair["message_id"] for pair in waits), "background-joined"


def verify_calls(messages: list[dict], parent_id: str, children: set[str], *,
                 delegations: list[dict] = (), processes: list[dict] = (),
                 allow_background_update: bool = False, child_links: list[dict] = ()) -> dict:
    calls = terminal_calls([row for row in messages if row["session_id"] in {parent_id, *children}])
    updates = [row for row in calls if row["command"] == UPDATE_COMMAND and row["session_id"] == parent_id]
    if len(updates) != 1:
        raise RuntimeError("the same parent must complete exactly one successful real update child")
    after_id, mode = _update_completion(updates[0], messages, parent_id, processes, allow_background_update)
    after = [row for row in calls if row["message_id"] > after_id and row["exit_code"] == 0]
    config = [row for row in after if row["session_id"] == parent_id and row["command"] == SET_COMMAND]
    delegated = [row for row in after if row["session_id"] in children and row["command"] == GET_COMMAND
                 and row["output"].strip().splitlines()[-1:] == ["23"]]
    if not config or not delegated:
        raise RuntimeError("post-update parent config write and delegated config read are both required")
    completed = completed_delegations(messages, parent_id, after_id, delegations,
        {row["session_id"] for row in delegated}, child_links)
    if not completed:
        raise RuntimeError("post-update parent must complete an actual delegate_task call")
    return {"completed_parent_delegations": completed, "update_message_id": after_id,
            "update_mode": mode, "successful_update_calls": 1,
            "post_update_parent_config_writes": len(config), "post_update_child_config_reads": len(delegated)}
