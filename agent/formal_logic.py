"""Opt-in formal checkpoints and bounded repair within the native conversation loop."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any

from agent.formal_logic_lean import verify
from agent.formal_logic_review import review
from agent.formal_logic_schema import LogicFormatError, parse_checkpoint

_BLOCK = re.compile(r"^```hermes_logic\s*\n(.*?)\n```[ \t]*$", re.MULTILINE | re.DOTALL)
_FAILURE = "Formal verification is incomplete. No verified conclusion is available."
_INSTRUCTIONS = """
Experimental formal logic mode is active. Produce concise, inspectable intermediate
checkpoints, not private reasoning. Separate empirical assumptions from deductions.
At each major inference and before a final answer or stakeholder question, emit exactly
one fenced hermes_logic JSON block. The runtime verifies it automatically and returns
repair feedback; do not run or modify the checker yourself. Use only the grammar below.
Include an answer outside the block that says only what the conclusion establishes.
Version 1 is finite propositional logic, at most 10 atoms and 16 hypotheses:
{"version":1,"atoms":{"P":"meaning of P","Q":"meaning of Q"},
 "assumptions":[{"id":"a","statement":"P entails Q","formula":["implies","P","Q"],
 "kind":"given","quote":"exact explicit stipulation from the current user task"}],
 "hypotheses":[{"id":"h","statement":"P entails Q","formula":["implies","P","Q"],"from":["a"]}],
 "conclusion":"h"}
Formulas: atom name, true/false, ["not",p], ["and",p,q], ["or",p,q],
["implies",p,q], ["iff",p,q]. from references assumptions or earlier hypotheses.
Every empirical implication must be an explicit assumption. Never hide an unsupported
conclusion as a given, change an atom's meaning, weaken the requested claim, or infer
from inconsistent assumptions. Fact assumptions use kind=fact and query=public search
query; stakeholder assumptions use kind=stakeholder and question=a question only the
actual stakeholder can answer. Given assumptions need kind=given and quote=an exact
explicit stipulation in the user's task. Ordinary tool discovery remains available.
Logic failure, checker failure and unsupported public facts go to automatic repair,
never to a human. Web assumption review follows successful Lean checks automatically.
Only after feedback says needs_stakeholder may clarify ask its exact pending question
with choices ["Accept assumption","Reject assumption"]. Proof validity remains
conditional on reviewed assumptions; it never proves external facts.
"""


@dataclass
class FormalState:
    lean_path: str
    max_repairs: int
    task: str = ""
    attempts: int = 0
    approved: bool = False
    finalized: bool = False
    accepted_response: str = ""
    status: str = "checkpoint_required"
    pending: dict = field(default_factory=dict)
    decisions: dict = field(default_factory=dict)
    cache: dict = field(default_factory=dict)
    last_receipt: dict = field(default_factory=dict)


def state(agent) -> FormalState | None:
    existing = getattr(agent, "_formal_logic_state", None)
    return existing if isinstance(existing, FormalState) else None


def begin_turn(agent, user_message: Any) -> str:
    if not hasattr(agent, "_formal_logic_initialized"):
        from hermes_cli.config import load_config_readonly
        loaded = load_config_readonly()
        section = loaded.get("agent") if isinstance(loaded, dict) else None
        config = section.get("formal_logic", {}) if isinstance(section, dict) else {}
        agent._formal_logic_state = None
        if isinstance(config, dict) and config.get("enabled") is True:
            repairs = config.get("max_repairs", 3)
            if type(repairs) is not int or not 1 <= repairs <= 8:
                raise ValueError("agent.formal_logic.max_repairs must be between 1 and 8")
            agent._formal_logic_state = FormalState(str(config.get("lean_path") or "lean"), repairs)
        agent._formal_logic_initialized = True
    current = state(agent)
    if current is None:
        return ""
    if getattr(agent, "api_mode", None) == "codex_app_server":
        raise RuntimeError("Formal logic mode requires the native Hermes loop; managed Codex/Claude children remain available")
    current.task = user_message if isinstance(user_message, str) else json.dumps(user_message, ensure_ascii=False)
    current.attempts, current.approved, current.status = 0, False, "checkpoint_required"
    current.finalized, current.accepted_response = False, ""
    current.pending, current.decisions, current.cache, current.last_receipt = {}, {}, {}, {}
    return "\n\n" + _INSTRUCTIONS


def check_candidate(agent, content: str) -> tuple[bool, str]:
    current = state(agent)
    if current is None:
        return True, content
    current.approved, current.pending = False, {}
    current.finalized, current.accepted_response = False, ""
    current.attempts += 1
    try:
        blocks = list(_BLOCK.finditer(content or ""))
        if len(blocks) != 1 or len(content) > 80_000:
            raise LogicFormatError("Return exactly one bounded fenced hermes_logic checkpoint")
        checkpoint = parse_checkpoint(blocks[0].group(1))
        answer = (content[:blocks[0].start()] + content[blocks[0].end():]).strip()
        if not answer:
            raise LogicFormatError("Supply a concise answer outside the checkpoint")
        receipt = current.cache.get(checkpoint.digest)
        if receipt is None:
            receipt = verify(checkpoint, current.lean_path)
            if receipt["status"] == "logic_verified":
                current.cache[checkpoint.digest] = receipt
        if receipt["status"] == "logic_verified":
            assessed = review(agent, checkpoint, answer, current.task, current.decisions)
            current.pending = assessed.pop("pending", {})
            receipt = {**receipt, "status": assessed["status"], "assumption_review": assessed}
        current.last_receipt = receipt
    except LogicFormatError as error:
        receipt = current.last_receipt = {"status": "format_failed", "error": str(error)}
    current.status = receipt["status"]
    if current.status == "reviewed":
        current.approved = True
        current.attempts = 0
        return True, content + "\n\nFormal check: Lean 4.35.0 verified the deductions; assumptions were reviewed separately."
    feedback = {"formal_logic": receipt, "pending_stakeholder_questions": list(current.pending),
                "instruction": "Repair the checkpoint/answer automatically. Ask no human about proof or checker failures."}
    if current.pending:
        feedback["instruction"] = "Logic and automatic review passed. Use clarify only for these exact stakeholder questions."
    return False, json.dumps(feedback, ensure_ascii=False)


def gate_final(agent, final_msg: dict, final_response: str, messages: list) -> tuple[bool, str] | None:
    current = state(agent)
    if current is None:
        return None
    accepted, text = check_candidate(agent, final_response)
    if accepted:
        current.finalized, current.accepted_response = True, text
        set_checked_message(final_msg, text)
        return False, text
    if current.attempts > current.max_repairs:
        return False, _FAILURE + f" Status: {current.status}."
    from agent.message_metadata import append_message
    final_msg["finish_reason"] = "formal_verification_required"
    append_message(messages, final_msg)
    append_message(messages, {"role": "user", "content": text, "_formal_logic_feedback": True})
    agent._session_messages = messages
    return True, ""


def intermediate_gate(agent, assistant_message, messages: list, conversation_history) -> bool:
    current = state(agent)
    if current is None or not assistant_message.tool_calls:
        return False
    content = assistant_message.content or ""
    if "```hermes_logic" not in content:
        return False
    _, feedback = check_candidate(agent, content)
    if current.approved:
        feedback = json.dumps({"status": "reviewed", "checkpoint": current.last_receipt["checkpoint"],
                               "instruction": "Checkpoint accepted. Reissue any still-needed tool calls."})
    from agent.message_metadata import append_message
    message = agent._build_assistant_message(assistant_message, assistant_message.finish_reason)
    append_message(messages, message)
    for call in assistant_message.tool_calls:
        append_message(messages, {"role": "tool", "tool_call_id": call.id, "name": call.function.name,
                                  "content": feedback + "\nThis tool call was not executed during checkpoint review."})
    agent._session_messages = messages
    agent._flush_messages_to_session_db(messages, conversation_history)
    return True


def before_tool(agent, name: str, arguments: dict) -> str | None:
    current = state(agent)
    if current is None:
        return None
    current.finalized, current.accepted_response = False, ""
    if name == "tool_call":
        for call in arguments.get("calls", []):
            nested = call.get("name", "") if isinstance(call, dict) else ""
            if nested == "tool_call":
                return "Nested deferred dispatch is unavailable during formal review"
            block = before_tool(agent, nested, call.get("arguments", {})) if isinstance(call, dict) else None
            if block:
                return block
        return None
    if name not in {"clarify", "setup_beat", "kanban_request_review"}:
        return None
    if not isinstance(arguments, dict):
        return "Formal review needs structured question arguments"
    questions = arguments.get("questions")
    if name != "clarify" or not isinstance(questions, list) or not questions:
        return "Formal mode allows human assumption review only through its pending clarify questions."
    for question in questions:
        if (not isinstance(question, dict) or question.get("question") not in current.pending
                or question.get("choices") != ["Accept assumption", "Reject assumption"]):
            return "Verify logic and automatic assumption review first; use only exact pending stakeholder questions."
    return None


def observe_tool(agent, name: str, arguments: dict, result: Any, *, blocked: bool) -> None:
    current = state(agent)
    if current is None or name != "clarify" or blocked:
        return
    try:
        data = json.loads(result) if isinstance(result, str) else result
        for row in data.get("responses", []):
            key = current.pending.get(row.get("question"))
            if key is not None:
                current.decisions[key] = row.get("user_response") or "unanswered"
        current.pending = {}
    except (TypeError, ValueError, AttributeError):
        current.pending = {}


def budget_fallback(agent) -> str | None:
    current = state(agent)
    if current is not None and not current.finalized:
        return _FAILURE + f" Status: {current.status}."
    return None


def attach_result(agent, result: dict) -> None:
    current = state(agent)
    if current is not None:
        result["formal_logic"] = {**current.last_receipt, "accepted": current.finalized}
        if not current.finalized:
            result.setdefault("error", "formal_verification_incomplete")
            result["completed"] = False


def seal_response(agent, response):
    current = state(agent)
    if current is None:
        return response
    return current.accepted_response if current.finalized else budget_fallback(agent)


def repair_exhausted(agent) -> bool:
    current = state(agent)
    return current is not None and current.attempts > current.max_repairs


def enforce_outcome(agent, response, failed):
    if budget_fallback(agent) is not None:
        return seal_response(agent, response), True
    return response, failed


def reset_delivery(agent) -> None:
    current = state(agent)
    if current is not None:
        current.finalized, current.accepted_response = False, ""


def seal_result(agent, result):
    if state(agent) is not None and isinstance(result, dict):
        result["final_response"] = seal_response(agent, result.get("final_response"))
        attach_result(agent, result)
    return result


def set_checked_message(message: dict, text: str) -> None:
    # Preserve the native sidecar for routes whose visible answer was mislabeled as reasoning.
    if "api_content" in message and not message.get("content"):
        message["api_content"] = text
    else:
        message["content"] = text


def checked_transform(agent, response):
    return None if state(agent) is None else seal_response(agent, response)
