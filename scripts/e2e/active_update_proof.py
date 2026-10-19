"""Strict transcript oracles for a real, already-unlocked terminal update."""
from __future__ import annotations

import hashlib
import json

UPDATE_COMMAND = "hermes update --yes --keep-stash --backup --branch main"
GET_COMMAND = "hermes config get agent.max_turns"
SET_COMMAND = "hermes config set agent.max_turns 23"


def prefix_hash(messages: list[dict]) -> str:
    return hashlib.sha256(json.dumps(messages, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def terminal_calls(messages: list[dict]) -> list[dict]:
    replies = {(row["session_id"], row["tool_call_id"]): row for row in messages if row["role"] == "tool"}
    executed = []
    for row in messages:
        for call in json.loads(row.get("tool_calls") or "[]"):
            function = call["function"]
            reply = replies.get((row["session_id"], call["id"]))
            if function["name"] != "terminal" or reply is None:
                continue
            result, _ = json.JSONDecoder().raw_decode(reply["content"].lstrip())
            executed.append({"session_id": row["session_id"], "message_id": reply["id"],
                             "command": json.loads(function["arguments"]).get("command", "").strip(),
                             "exit_code": result.get("exit_code") if type(result.get("exit_code")) is int else None,
                             "output": str(result.get("output", ""))})
    return executed


def completed_delegations(messages: list[dict], parent_id: str, after: int) -> int:
    replies = {row["tool_call_id"]: row for row in messages
               if row["role"] == "tool" and row["session_id"] == parent_id and row["id"] > after}
    completed = 0
    for row in messages:
        if row["session_id"] != parent_id or row["id"] <= after:
            continue
        for call in json.loads(row.get("tool_calls") or "[]"):
            reply = replies.get(call["id"])
            if call["function"]["name"] == "delegate_task" and reply is not None:
                result, _ = json.JSONDecoder().raw_decode(reply["content"].lstrip())
                entries = result.get("results")
                completed += bool(isinstance(entries, list) and entries and all(
                    isinstance(entry, dict) and entry.get("status") == "completed" for entry in entries))
    return completed


def verify_calls(messages: list[dict], parent_id: str, children: set[str]) -> dict:
    calls = terminal_calls([row for row in messages if row["session_id"] in {parent_id, *children}])
    updates = [row for row in calls if row["command"] == UPDATE_COMMAND and row["session_id"] == parent_id]
    if len(updates) != 1 or updates[0]["exit_code"] != 0:
        raise RuntimeError("the same parent must complete exactly one successful real update child")
    after = [row for row in calls if row["message_id"] > updates[0]["message_id"] and row["exit_code"] == 0]
    config = [row for row in after if row["session_id"] == parent_id and row["command"] == SET_COMMAND]
    delegated = [row for row in after if row["session_id"] in children and row["command"] == GET_COMMAND
                 and row["output"].strip().splitlines()[-1:] == ["23"]]
    if not config or not delegated:
        raise RuntimeError("post-update parent config write and delegated config read are both required")
    completed = completed_delegations(messages, parent_id, updates[0]["message_id"])
    if not completed:
        raise RuntimeError("post-update parent must complete an actual delegate_task call")
    return {"completed_parent_delegations": completed, "update_message_id": updates[0]["message_id"], "successful_update_calls": 1,
            "post_update_parent_config_writes": len(config), "post_update_child_config_reads": len(delegated)}
