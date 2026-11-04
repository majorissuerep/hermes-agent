"""A stopped turn cannot begin review, accept an answer, or open stakeholder questions."""
import json
from types import SimpleNamespace

import pytest

from agent import formal_logic as mode


@pytest.mark.parametrize(("stage", "review_status"), [
    ("before-checker", "needs_stakeholder"), ("after-checker", "needs_stakeholder"),
    ("after-review", "needs_stakeholder"), ("after-review", "reviewed"),
])
def test_interrupt_fences_formal_phase_transitions(monkeypatch, stage, review_status):
    checkpoint = {"version": 1, "atoms": {"P": "The user approves the plan"},
                  "assumptions": [{"id": "a", "statement": "The user approves the plan", "kind": "stakeholder",
                                   "question": "Do you approve this plan?", "formula": "P"}],
                  "hypotheses": [{"id": "h", "statement": "The plan is conditionally approved",
                                  "formula": "P", "from": ["a"]}], "conclusion": "h"}
    fence = chr(96) * 3
    content = "The plan is conditionally approved.\n" + fence + "hermes_logic\n" + json.dumps(checkpoint) + "\n" + fence
    agent = SimpleNamespace(_formal_logic_state=mode.FormalState("lean", 2),
                            _interrupt_requested=stage == "before-checker")
    calls = []

    def verify(*args):
        calls.append("checker")
        if stage == "after-checker":
            agent._interrupt_requested = True
        return {"status": "logic_verified", "checkpoint": "unit-fixture"}

    def review(*args):
        calls.append("review")
        if stage == "after-review":
            agent._interrupt_requested = True
        return {"status": review_status, "pending": {"Do you approve this plan?": ("a",)}}

    monkeypatch.setattr(mode, "verify", verify)
    monkeypatch.setattr(mode, "review", review)
    messages = []
    again, answer = mode.gate_final(agent, {"role": "assistant", "content": content}, content, messages)
    assert not again and "interrupted" in answer
    assert calls == {"before-checker": [], "after-checker": ["checker"],
                     "after-review": ["checker", "review"]}[stage]
    current = mode.state(agent)
    assert current.status == "interrupted" and not current.pending and not current.finalized
    assert current.attempts == 0 and messages == []
    assert mode.before_tool(agent, "clarify", {"questions": [
        {"question": "Do you approve this plan?", "choices": ["Accept assumption", "Reject assumption"]}]})
