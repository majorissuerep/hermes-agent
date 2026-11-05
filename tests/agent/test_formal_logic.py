"""Formal mode checks logic before evidence or human attention, and fails closed."""

import copy
import json
from types import SimpleNamespace

import pytest

from agent import formal_logic as mode
from agent.formal_logic_schema import LogicFormatError, counterexample, parse_checkpoint


def capsule():
    return {"version": 1, "atoms": {"P": "P holds", "Q": "Q holds"},
            "assumptions": [
                {"id": "a", "statement": "P holds", "formula": "P", "kind": "given", "quote": "Assume P"},
                {"id": "b", "statement": "P implies Q", "formula": ["implies", "P", "Q"],
                 "kind": "given", "quote": "P implies Q"}],
            "hypotheses": [{"id": "h", "statement": "Q holds", "formula": "Q", "from": ["a", "b"]}],
            "conclusion": "h"}


def candidate(data=None):
    return "Therefore Q holds conditionally.\n\n```hermes_logic\n" + json.dumps(data or capsule()) + "\n```"


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(conclusion=[]),
    lambda d: d["assumptions"][0].update(kind=[]),
    lambda d: d["hypotheses"][0].update(**{"from": ["h"]}),
    lambda d: d["hypotheses"][0].update(formula=["run", "IO.println"]),
    lambda d: d["hypotheses"].append(copy.deepcopy(d["hypotheses"][0])),
    lambda d: d["atoms"].update({"X; axiom forged : False": "injection"}),
])
def test_untrusted_checkpoint_cannot_introduce_code_or_self_proof(mutation):
    data = capsule()
    mutation(data)
    with pytest.raises(LogicFormatError):
        parse_checkpoint(json.dumps(data))


def test_truth_checks_reject_missing_premise_and_contradictory_context():
    valid = capsule()
    assert counterexample(parse_checkpoint(json.dumps(valid))) is None
    invalid = copy.deepcopy(valid)
    invalid["hypotheses"][0]["from"] = ["a"]
    failure = counterexample(parse_checkpoint(json.dumps(invalid)))
    assert failure["error"] == "invalid_deduction"
    assert failure["counterexample"]["P"] and not failure["counterexample"]["Q"]
    invalid["assumptions"][1]["formula"] = ["not", "P"]
    assert counterexample(parse_checkpoint(json.dumps(invalid)))["error"] == "inconsistent_assumptions"


def test_failed_kernel_does_not_run_review_or_allow_human_question(monkeypatch):
    agent = SimpleNamespace(_formal_logic_state=mode.FormalState("lean", 2))
    monkeypatch.setattr(mode, "verify", lambda *args: {"status": "checker_failed", "error": "false canary accepted"})
    monkeypatch.setattr(mode, "review", lambda *args: pytest.fail("review must follow kernel acceptance"))
    accepted, feedback = mode.check_candidate(agent, candidate())
    assert not accepted and "checker_failed" in feedback
    assert mode.before_tool(agent, "clarify", {"questions": [{"question": "Please fix Lean"}]})
    assert mode.budget_fallback(agent) and not agent._formal_logic_state.finalized


def test_repair_budget_never_releases_rejected_answer(monkeypatch):
    agent = SimpleNamespace(_formal_logic_state=mode.FormalState("lean", 1))
    monkeypatch.setattr(mode, "verify", lambda *args: {"status": "logic_failed"})
    messages = []
    first = mode.gate_final(agent, {"role": "assistant", "content": candidate()}, candidate(), messages)
    assert first == (True, "") and [m["role"] for m in messages] == ["assistant", "user"]
    again, text = mode.gate_final(agent, {"role": "assistant", "content": candidate()}, candidate(), messages)
    assert not again and "No verified conclusion" in text
    assert "Therefore Q" not in text
    result = {"completed": True}
    mode.attach_result(agent, result)
    assert result["completed"] is False and result["formal_logic"]["accepted"] is False


def test_session_toggle_preserves_existing_session_policy(monkeypatch):
    config = {"agent": {"formal_logic": {"enabled": False}}}
    monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda: copy.deepcopy(config))
    agent = SimpleNamespace()
    assert mode.begin_turn(agent, "first") == ""
    config["agent"]["formal_logic"]["enabled"] = True
    assert mode.begin_turn(agent, "second") == ""
    other = SimpleNamespace()
    assert "hermes_logic" in mode.begin_turn(other, "Assume P; P implies Q")
    assert mode.state(other).task == "Assume P; P implies Q"


def test_human_review_requires_actual_exact_pending_question_and_result(monkeypatch):
    agent = SimpleNamespace(_formal_logic_state=mode.FormalState("lean", 3))
    key = ("a", "Proceed with the plan", "May we proceed?")
    current = mode.state(agent)
    current.pending = {"May we proceed?": key}
    args = {"questions": [{"question": "May we proceed?", "choices": ["Accept assumption", "Reject assumption"]}]}
    assert mode.before_tool(agent, "clarify", args) is None
    mode.observe_tool(agent, "clarify", args, json.dumps({"responses": [
        {"question": "May we proceed?", "status": "answered", "user_response": "Accept assumption"}]}), blocked=True)
    assert not current.decisions
    mode.observe_tool(agent, "clarify", args, json.dumps({"responses": [
        {"question": "May we proceed?", "status": "answered", "user_response": "Accept assumption"}]}), blocked=False)
    assert current.decisions[key] == "Accept assumption"
    assert mode.before_tool(agent, "clarify", args)


def test_deferred_dispatch_cannot_bypass_human_gate():
    agent = SimpleNamespace(_formal_logic_state=mode.FormalState("lean", 2))
    arguments = {"calls": [{"name": "clarify", "arguments": {
        "questions": [{"question": "Please fix my proof"}]}}]}
    assert mode.before_tool(agent, "tool_call", arguments)


def test_plugin_argument_rewrite_cannot_replace_pending_stakeholder_question(monkeypatch):
    from agent.tool_executor import _pre_tool_block

    agent = SimpleNamespace(_formal_logic_state=mode.FormalState("lean", 2))
    mode.state(agent).pending = {"An authorized question": ("a",)}
    args = {"questions": [{"question": "An authorized question",
                          "choices": ["Accept assumption", "Reject assumption"]}]}
    ref = SimpleNamespace(name="clarify", args=args, task_id="t", call_id="c", trace=[])
    monkeypatch.setattr("agent.tool_executor.tool_hook_ids", lambda *args: {})
    monkeypatch.setattr("hermes_cli.plugins._dispatch_pre_tool_call_hooks",
                        lambda *args, **kwargs: (None, {"questions": [{"question": "An unrelated question"}]}))
    assert _pre_tool_block(agent, ref)[0]


def test_a_later_rejected_candidate_cannot_reuse_a_previous_final_acceptance(monkeypatch):
    agent = SimpleNamespace(_formal_logic_state=mode.FormalState("lean", 2))
    monkeypatch.setattr(mode, "verify", lambda *args: {"status": "logic_verified", "checkpoint": "fixture"})
    monkeypatch.setattr(mode, "review", lambda *args: {"status": "reviewed"})
    assert mode.gate_final(agent, {}, candidate(), []) is not None
    assert mode.state(agent).finalized
    mode.check_candidate(agent, "An unverified changed answer")
    assert mode.budget_fallback(agent) is not None


def test_checked_output_cannot_be_rewritten_after_verification(monkeypatch):
    from agent.turn_finalizer import apply_llm_output_transform

    agent = SimpleNamespace(_formal_logic_state=mode.FormalState("lean", 2))
    current = mode.state(agent)
    current.finalized, current.accepted_response = True, "A checked conditional conclusion"
    monkeypatch.setattr("agent.turn_finalizer._invoke_hook_safely",
                        lambda *args, **kwargs: ["An unrelated unchecked claim"])
    result, _, _ = apply_llm_output_transform(agent, "A checked conditional conclusion", turn_id="turn")
    assert result == current.accepted_response


def test_formal_gate_preserves_native_reasoning_answer_sidecar(monkeypatch):
    from agent.turn_stop_gates import apply_stop_gates

    agent = SimpleNamespace(_formal_logic_state=mode.FormalState("lean", 2))
    monkeypatch.setattr(mode, "verify", lambda *args: {"status": "logic_verified", "checkpoint": "fixture"})
    monkeypatch.setattr(mode, "review", lambda *args: {"status": "reviewed"})
    raw = candidate()
    message = {"role": "assistant", "content": "", "api_content": raw}
    verdict = apply_stop_gates(agent, message, final_response=raw, messages=[], conversation_history=[],
                               pending_verification_response=None, pending_verification_response_previewed=False)
    assert not verdict.continue_turn and message["content"] == verdict.final_response
    assert message["api_content"] == raw


def test_review_error_credentials_are_removed_before_repair_feedback(monkeypatch):
    agent = SimpleNamespace(_formal_logic_state=mode.FormalState("lean", 3))
    mode.state(agent).task = "Assume P; P implies Q. Show Q."
    monkeypatch.setattr(mode, "verify", lambda *args: {"status": "logic_verified", "checkpoint": "fixture"})
    monkeypatch.setattr("agent.redact._redact_enabled", lambda: False)
    def failed_review(*args):
        raise RuntimeError("Review request failed: https://fixture-user:fixture-password@review.invalid/check?api_key=fixture-query-key")
    monkeypatch.setattr("agent.formal_logic_review._judge", failed_review)
    messages = []
    assert mode.gate_final(agent, {"role": "assistant", "content": candidate()}, candidate(), messages) == (True, "")
    feedback = messages[-1]["content"]
    assert "review_unavailable" in feedback and "Review request failed" in feedback
    assert all(secret not in feedback for secret in ("fixture-user", "fixture-password", "fixture-query-key"))
    assert mode.before_tool(agent, "clarify", {"questions": [{"question": "Fix the review service?"}]})
