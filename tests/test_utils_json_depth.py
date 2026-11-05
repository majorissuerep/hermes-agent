"""Excessive JSON nesting remains a parse failure for tool-result consumers."""


def test_deep_tool_result_returns_fallback_without_losing_failure_classification():
    from agent.tool_guardrails import classify_tool_failure
    from utils import safe_json_loads

    deep = "[" * 100_000 + "]" * 100_000
    fallback = object()
    assert safe_json_loads(deep, fallback) is fallback
    assert classify_tool_failure("terminal", deep) == (False, "")
    assert classify_tool_failure("terminal", '{"exit_code": 0}') == (False, "")
    assert classify_tool_failure("terminal", '{"exit_code": 7}') == (True, " [exit 7]")
