"""Malformed model Unicode returns repair feedback before checker or stakeholder calls."""
import json
from types import SimpleNamespace

import pytest

from agent import formal_logic as mode
from agent.formal_logic_schema import parse_checkpoint


def _checkpoint(meaning):
    return {"version": 1, "atoms": {"P": meaning}, "assumptions": [
        {"id": "a", "statement": "P holds", "formula": "P",
         "kind": "given", "quote": "Assume P"}],
        "hypotheses": [{"id": "h", "statement": "P holds", "formula": "P", "from": ["a"]}],
        "conclusion": "h"}


@pytest.mark.parametrize("escaped", [True, False], ids=["escaped-surrogate", "literal-surrogate"])
def test_invalid_unicode_returns_automatic_repair_without_human_or_checker(monkeypatch, escaped):
    body = json.dumps(_checkpoint(chr(0xD800)), ensure_ascii=escaped)
    fence = chr(96) * 3
    content = "P follows conditionally.\n" + fence + "hermes_logic\n" + body + "\n" + fence
    agent = SimpleNamespace(_formal_logic_state=mode.FormalState("lean", 2))
    monkeypatch.setattr(mode, "verify", lambda *args: pytest.fail("Malformed Unicode reached checker"))
    monkeypatch.setattr(mode, "review", lambda *args: pytest.fail("Malformed Unicode reached reviewer"))
    messages = []
    assert mode.gate_final(agent, {"role": "assistant", "content": content}, content, messages) == (True, "")
    feedback = json.loads(messages[-1]["content"])
    assert feedback["formal_logic"]["status"] == "format_failed"
    assert "Unicode" in feedback["formal_logic"]["error"]
    assert feedback["pending_stakeholder_questions"] == []
    assert mode.before_tool(agent, "clarify", {"questions": [{"question": "Fix this input?"}]})
    assert not mode.state(agent).finalized


def test_valid_unicode_remains_lossless_and_digest_stable():
    meaning = "Дозвіл на дію 🧪"
    escaped = parse_checkpoint(json.dumps(_checkpoint(meaning), ensure_ascii=True))
    literal = parse_checkpoint(json.dumps(_checkpoint(meaning), ensure_ascii=False))
    assert escaped.atoms["P"] == literal.atoms["P"] == meaning
    assert escaped.digest == literal.digest
