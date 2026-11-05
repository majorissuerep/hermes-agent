"""Required stakeholder responses do not consume automatic proof-repair attempts."""
import json
from types import SimpleNamespace

from agent import formal_logic as mode


def test_valid_stakeholder_review_rounds_preserve_repair_budget(monkeypatch):
    data = {"version": 1, "atoms": {"P": "The user approves the plan", "Q": "The user approves the budget"},
            "assumptions": [
                {"id": "a", "statement": "The user approves the plan", "kind": "stakeholder",
                 "question": "Do you approve the plan?", "formula": "P"},
                {"id": "b", "statement": "The user approves the budget", "kind": "stakeholder",
                 "question": "Do you approve the budget?", "formula": "Q"}],
            "hypotheses": [{"id": "h", "statement": "The user approves both", "formula": ["and", "P", "Q"],
                            "from": ["a", "b"]}], "conclusion": "h"}
    fence = chr(96) * 3
    content = "Both approvals hold conditionally.\n" + fence + "hermes_logic\n" + json.dumps(data) + "\n" + fence
    agent = SimpleNamespace(_formal_logic_state=mode.FormalState("lean", 1))
    monkeypatch.setattr(mode, "verify", lambda *args: {"status": "logic_verified", "checkpoint": "unit-fixture"})
    monkeypatch.setattr("agent.formal_logic_review._judge", lambda *args: {
        "alignment": "faithful", "assumptions": [
            {"id": name, "verdict": "stakeholder_required"} for name in ("a", "b")]})
    messages = []
    assert mode.gate_final(agent, {"role": "assistant", "content": "Missing checkpoint"},
                           "Missing checkpoint", messages) == (True, "")
    assert mode.state(agent).attempts == 1
    for index, question in enumerate(("Do you approve the plan?", "Do you approve the budget?")):
        verdict = mode.gate_final(agent, {"role": "assistant", "content": content}, content, messages)
        assert verdict == (True, ""), "A verified checkpoint awaiting a required stakeholder is not a failed repair"
        assert mode.state(agent).status == "needs_stakeholder"
        assert mode.state(agent).attempts == 1
        assert len(mode.state(agent).pending) == 2 - index
        args = {"questions": [{"question": question, "choices": ["Accept assumption", "Reject assumption"]}]}
        assert mode.before_tool(agent, "clarify", args) is None
        mode.observe_tool(agent, "clarify", args, {"responses": [
            {"question": question, "status": "answered", "user_response": "Accept assumption"}]}, blocked=False)
    accepted, answer = mode.gate_final(agent, {"role": "assistant", "content": content}, content, messages)
    assert not accepted and "Formal check:" in answer
    assert mode.state(agent).finalized
