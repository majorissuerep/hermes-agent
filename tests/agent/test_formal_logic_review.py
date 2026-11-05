"""An evidence reviewer cannot turn missing sources or public unknowns into approval."""

import json
from types import SimpleNamespace

import pytest

from agent.formal_logic_review import _validate_review, review
from agent.formal_logic_schema import parse_checkpoint


def checkpoint(kind="fact"):
    extra = {"fact": {"query": "public fact"}, "stakeholder": {"question": "Is this your preference?"},
             "given": {"quote": "Assume P"}}[kind]
    return parse_checkpoint(json.dumps({"version": 1, "atoms": {"P": "P holds"},
        "assumptions": [{"id": "a", "statement": "P holds", "kind": kind, "formula": "P", **extra}],
        "hypotheses": [{"id": "h", "statement": "P holds conditionally", "formula": "P", "from": ["a"]}],
        "conclusion": "h"}))


def judgment(verdict="supported", citations=None):
    return {"alignment": "faithful", "assumptions": [
        {"id": "a", "verdict": verdict, "citations": citations or [], "reason": "Evidence assessed",
         "evidence": [{"url": "https://example.org/fact", "quote": "The source establishes this public fact."}]}]}


def test_missing_or_fabricated_evidence_cannot_qualify_fact():
    data = checkpoint()
    with pytest.raises(ValueError):
        _validate_review(data, judgment(citations=["https://example.org/fact"]), {"a": []}, {})
    with pytest.raises(ValueError):
        _validate_review(data, judgment(citations=["https://forged.example/"]), {
            "a": [{"urls": ["https://example.org/fact"]}]}, {})
    result = _validate_review(data, judgment(citations=["https://example.org/fact"]), {
        "a": [{"urls": ["https://example.org/fact"], "extracted": [{"url": "https://example.org/fact",
         "content": "The source establishes this public fact."}]}]}, {})
    assert result["status"] == "reviewed"
    assert _validate_review(data, judgment("stakeholder_required"), {"a": []}, {})["status"] == "assumptions_failed"


def test_given_requires_quote_from_real_task_before_any_service_call(monkeypatch):
    monkeypatch.setattr("agent.formal_logic_review._judge", lambda *args: pytest.fail("fabricated given reached reviewer"))
    result = review(SimpleNamespace(), checkpoint("given"), "P", "Different user task", {})
    assert result["status"] == "assumptions_failed"


def test_stakeholder_requires_independent_necessity_and_explicit_acceptance():
    data = checkpoint("stakeholder")
    assert _validate_review(data, judgment(), {}, {})["status"] == "assumptions_failed"
    pending = _validate_review(data, judgment("stakeholder_required"), {}, {})
    assert pending["status"] == "needs_stakeholder"
    key = pending["pending"]["Is this your preference?"]
    assert _validate_review(data, judgment("stakeholder_required"), {}, {key: "Reject assumption"})["status"] == "assumptions_failed"
    assert _validate_review(data, judgment("stakeholder_required"), {}, {key: "Accept assumption"})["status"] == "reviewed"


def test_fact_flow_retrieves_pages_and_validates_exact_passages(monkeypatch):
    from agent.formal_logic_review import _fact_evidence
    calls = []
    text = "The source establishes this public fact."
    monkeypatch.setattr("tools.web_tools.web_search_tool", lambda query, limit: json.dumps({
        "success": True, "data": {"web": [{"url": "https://example.org/fact"}]}}))
    async def extract(urls, char_limit):
        calls.append(urls)
        return json.dumps({"results": [{"url": urls[0], "content": text, "error": None}]})
    monkeypatch.setattr("tools.web_tools.web_extract_tool", extract)
    data = checkpoint()
    evidence = {"a": _fact_evidence(data.assumptions[0])}
    assert calls == [["https://example.org/fact"]]
    assert _validate_review(data, judgment(citations=["https://example.org/fact"]), evidence, {})["status"] == "reviewed"
    fabricated = judgment(citations=["https://example.org/fact"])
    fabricated["assumptions"][0]["evidence"][0]["quote"] = "A fabricated passage never present in the source."
    with pytest.raises(ValueError):
        _validate_review(data, fabricated, evidence, {})


@pytest.mark.parametrize(("task", "answer"), [
    ("Assume P " + "x" * 16_000, "P follows conditionally"),
    ("Assume P", "P follows conditionally " + "x" * 16_000),
], ids=["oversized-task", "oversized-answer"])
def test_oversized_inputs_cannot_be_silently_truncated_before_review(monkeypatch, task, answer):
    monkeypatch.setattr("agent.formal_logic_review._judge",
                        lambda *args: pytest.fail("Oversized input must not reach a truncated review"))
    result = review(SimpleNamespace(), checkpoint("given"), answer, task, {})
    assert result["status"] == "assumptions_failed"
    assert "16000" in result["error"]
