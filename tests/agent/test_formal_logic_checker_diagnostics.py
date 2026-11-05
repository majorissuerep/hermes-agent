"""Malformed compiler diagnostics require bounded automatic repair, never human review.

The compiler, PM selection and workspace are intercepted. These are public-gate
orchestration contracts, not native Lean or process-isolation qualification.
"""
from contextlib import contextmanager
import hashlib
import json
from subprocess import CompletedProcess
from types import SimpleNamespace

import pytest

from agent import formal_logic as mode
from agent import formal_logic_lean as lean
from agent.formal_logic_schema import parse_checkpoint
from agent.turn_stop_gates import apply_stop_gates


def _subject(monkeypatch, tmp_path, malformed=None, phase="proof"):
    data = {"version": 1, "atoms": {"P": "P holds", "Q": "Q holds"},
            "assumptions": [
                {"id": "a", "statement": "P holds", "formula": "P", "kind": "given", "quote": "Assume P"},
                {"id": "b", "statement": "P implies Q", "formula": ["implies", "P", "Q"],
                 "kind": "given", "quote": "P implies Q"}],
            "hypotheses": [{"id": "h", "statement": "Q holds", "formula": "Q", "from": ["a", "b"]}],
            "conclusion": "h"}
    checkpoint = parse_checkpoint(json.dumps(data))
    generated, expected = lean.lean_source(checkpoint)
    content = "Therefore Q holds conditionally.\n```hermes_logic\n" + json.dumps(data) + "\n```"
    calls, reviews = [], []
    selected = SimpleNamespace(executable=object(), receipt=lambda: {"scope": "intercepted-not-native-PM-proof"})
    monkeypatch.setattr(lean, "installed_checker", lambda *args, **kwargs: selected)

    @contextmanager
    def workspace():
        yield tmp_path, "intercepted-not-RAM-proof"

    monkeypatch.setattr(lean, "workspace", workspace)

    def compiler(_executable, arguments, *, directory, source=None, timeout=30):
        calls.append((arguments, source, timeout))
        if arguments == ["--version"]:
            return CompletedProcess([], 0, f"Lean (version {lean.LEAN_VERSION}, commit {lean.LEAN_COMMIT})", "")
        canary = "example : False" in source
        if not canary:
            assert source == generated
        # Missing data on an error and missing severity on a report retain the
        # established optional-field semantics. Every generated obligation is present.
        rows = [{"severity": "error"}] if canary else [
            {"data": f"'{name}' does not depend on any axioms"} for name in sorted(expected)] + [
                {"severity": "information"}]
        if malformed is not None and ("canary" if canary else "proof") == phase:
            rows.append(malformed)
        return CompletedProcess([], int(canary), "\n".join(json.dumps(row) for row in rows), "")

    monkeypatch.setattr(lean, "run_checker", compiler)

    def review(_agent, actual, answer, task, decisions):
        reviews.append(actual.digest)
        assert actual.digest == checkpoint.digest and "Q holds conditionally" in answer
        return {"status": "reviewed", "pending": {}}

    monkeypatch.setattr(mode, "review", review)
    agent = SimpleNamespace(
        _formal_logic_state=mode.FormalState("lean", 1),
        _build_assistant_message=lambda message, finish: {"role": "assistant", "content": message.content},
        _flush_messages_to_session_db=lambda *args: None)
    mode.state(agent).task = "Assume P. P implies Q. Show Q conditionally."
    return agent, content, checkpoint, generated, expected, calls, reviews


def _public_gate(agent, content, boundary, messages):
    if boundary == "final-stop":
        return apply_stop_gates(
            agent, {"role": "assistant", "content": content}, final_response=content, messages=messages,
            conversation_history=[], pending_verification_response=None,
            pending_verification_response_previewed=False)
    call = SimpleNamespace(id="inert-call", function=SimpleNamespace(name="web_search"))
    message = SimpleNamespace(content=content, tool_calls=[call], finish_reason="tool_calls")
    return mode.intermediate_gate(agent, message, messages, [])


@pytest.mark.parametrize("boundary", ["final-stop", "intermediate-tools"])
@pytest.mark.parametrize("phase", ["canary", "proof"])
@pytest.mark.parametrize("malformed", [
    {"severity": "information", "data": None},
    {"severity": "information", "data": 17},
    {"severity": [], "data": "invalid severity"},
    {"severity": {}, "data": "invalid severity"},
], ids=["null-data", "integer-data", "list-severity", "object-severity"])
def test_malformed_checker_output_consumes_repair_budget_without_review_or_human(
        monkeypatch, tmp_path, boundary, phase, malformed):
    agent, content, _, _, _, calls, reviews = _subject(monkeypatch, tmp_path, malformed, phase)
    messages = []
    first = _public_gate(agent, content, boundary, messages)
    current = mode.state(agent)
    assert reviews == [] and not current.approved and not current.pending and not current.cache
    assert current.attempts == 1 and current.last_receipt["status"] == "checker_failed"
    assert current.last_receipt["proof_status"] == "unknown"
    assert current.last_receipt["error"] == "Invalid Lean diagnostic stream"
    assert current.last_receipt["checker"]["invocations"] == (2 if phase == "canary" else 3)
    assert any("checker_failed" in row.get("content", "") for row in messages)
    assert mode.before_tool(agent, "clarify", {"questions": [
        {"question": "Repair the checker?", "choices": ["Accept assumption", "Reject assumption"]}]})
    assert first.continue_turn if boundary == "final-stop" else first is True
    second = _public_gate(agent, content, boundary, messages)
    assert current.attempts == 2 and mode.repair_exhausted(agent) and reviews == []
    assert len(calls) == (4 if phase == "canary" else 6)
    assert "No verified conclusion" in mode.budget_fallback(agent)
    if boundary == "final-stop":
        assert not second.continue_turn and "No verified conclusion" in second.final_response
    result = {"completed": True}
    mode.attach_result(agent, result)
    assert result["completed"] is False and result["formal_logic"]["accepted"] is False


@pytest.mark.parametrize("boundary", ["final-stop", "intermediate-tools"])
def test_healthy_complete_axiom_free_reports_still_certify_conditional_deduction(monkeypatch, tmp_path, boundary):
    agent, content, checkpoint, generated, expected, calls, reviews = _subject(monkeypatch, tmp_path)
    messages = []
    verdict = _public_gate(agent, content, boundary, messages)
    current = mode.state(agent)
    receipt = current.last_receipt
    assert reviews == [checkpoint.digest] and current.approved and current.attempts == 0
    assert receipt["proof_status"] == "verified_finite_conditional_deductions"
    assert receipt["obligations"] == len(expected) == 2 and receipt["axioms"] == []
    assert receipt["source_sha256"] == hashlib.sha256(generated.encode()).hexdigest()
    assert receipt["checker"]["invocations"] == receipt["checker"]["maximum_invocations"] == 3
    assert [call[2] for call in calls] == [5, 30, 30]
    if boundary == "final-stop":
        assert not verdict.continue_turn and current.finalized and "Formal check:" in verdict.final_response
    else:
        assert verdict is True and not current.finalized
        assert json.loads(messages[-1]["content"].split("\nThis tool")[0])["status"] == "reviewed"
