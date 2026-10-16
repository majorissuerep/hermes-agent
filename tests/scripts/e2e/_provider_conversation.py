"""One encrypted growing-conversation contract across every supported wire route.

Only HTTP providers are synthetic: real agent, tools, compression, SQLCipher,
history reload, and cleanup execute unchanged on native Linux and macOS.
"""
from __future__ import annotations

from contextlib import ExitStack
import json
import re

from scripts.e2e.provider_conversation import TURNS, run_probe
from scripts.e2e.provider_matrix import CONNECTIONS
from tests.fakes.fake_llm_provider import Error, FakeLLMServer, Text as Summary
from tests.fakes.providers.anthropic_messages import AnthropicMessagesServer, ApiError, Reply, Text, ToolUse
from tests.fakes.providers.chat_variants import CError, CText, CTools, FakeChatVariantServer
from tests.fakes.providers.openai_responses import FakeResponsesServer, FunctionCall, HttpError, Message, Turn

def providers_for(mode):
    return sorted({connection.provider for connection in CONNECTIONS.values() if mode in connection.modes})


def _respond(record, mode):
    body = record["body"]
    encoded = json.dumps(body)
    tokens = max(1, len(encoded) // 4)
    history = body.get("input") or body.get("messages") or []
    last = history[-1]
    results = re.findall(r"RESULT=(VALUE-[a-f0-9]{32})", json.dumps(last))
    if "Recall continuity anchor:" in json.dumps(last):
        anchors = re.findall(r"CONTINUITY-[a-f0-9]{32}", encoded)
        answer = anchors[-1] if anchors else "ANCHOR-MISSING"
    elif results:
        answer = results[-1]
    else:
        paths = re.findall(r'(/[^\s"]*/reference-(\d+)\.txt)', encoded)
        path = max(paths, key=lambda item: int(item[1]))[0] if paths else "missing-file"
        args = {"path": path, "limit": 1000}
        return {
            "chat_completions": lambda: CTools([("read_file", args)]),
            "codex_responses": lambda: Turn([FunctionCall("read_file", args)], input_tokens=tokens),
            "anthropic_messages": lambda: Reply([ToolUse("read_file", args)], input_tokens=tokens),
        }[mode]()
    return {
        "chat_completions": lambda: CText(answer, prompt_tokens=tokens),
        "codex_responses": lambda: Turn([Message(answer)], input_tokens=tokens),
        "anthropic_messages": lambda: Reply([Text(answer)], input_tokens=tokens),
    }[mode]()


def assert_growing_conversation(tmp_path, provider, mode, inject_faults):
    summary_requests = []
    main_requests = []

    def summarize(record):
        summary_requests.append(record)
        if inject_faults and len(summary_requests) == 1:
            return Error(503, "temporary summarizer outage")
        anchors = re.findall(r"CONTINUITY-[a-f0-9]{32}", json.dumps(record["body"]))
        return Summary("## Goal\nRead each requested reference file and report its RESULT.\n"
                       "## Progress\n### Done\n- Read the earlier references and returned their values.\n"
                       f"## Important context\nContinuity anchor: {anchors[-1] if anchors else 'not present'}.\n"
                       "## Next Steps\n- Read the next requested file and preserve the continuity anchor.\n")

    def respond(record):
        main_requests.append(record)
        if inject_faults and len(main_requests) == 1:
            return {
                "chat_completions": lambda: CError(429, "temporary rate limit", code="rate_limit_exceeded", retry_after=0),
                "codex_responses": lambda: HttpError(429, "temporary rate limit", code="rate_limit_exceeded", retry_after=0),
                "anthropic_messages": lambda: ApiError(429, "rate_limit_error", "temporary rate limit", retry_after=0),
            }[mode]()
        return _respond(record, mode)

    with ExitStack() as stack:
        summary = stack.enter_context(FakeLLMServer(summarize, aux=summarize))
        server = stack.enter_context({
            "chat_completions": FakeChatVariantServer,
            "codex_responses": FakeResponsesServer,
            "anthropic_messages": AnthropicMessagesServer,
        }[mode](respond))
        route = {"provider": provider, "api_mode": mode, "model": "acceptance-model",
                 "api_key": "sk-synthetic-protocol", "base_url": server.base_url}
        summary_route = {**route, "provider": "custom", "api_mode": "chat_completions",
                         "base_url": summary.base_url}
        receipt = run_probe(tmp_path, route, summary_route=summary_route)
        errors = server.schema_errors() if mode == "anthropic_messages" else server.invalid_requests()
        if errors:
            print("PROTOCOL-SCHEMA", errors[0][1])
        shapes = []
        if errors and mode == "codex_responses":
            shapes = [{"role": item.get("role"), "type": item.get("type"), "id": item.get("id"),
                       "status": item.get("status"),
                       "parts": [part.get("type") for part in item.get("content", []) if isinstance(part, dict)]}
                      for item in server.main_requests()[errors[0][0]]["input"]]
        first_bad = re.search(r"input\[(\d+)\]", errors[0][1]) if errors and mode == "codex_responses" else None
        shape = shapes[int(first_bad[1])] if first_bad else shapes
        assert errors == [], f"Invalid item: {shape}; details: {str(errors[:1])[-650:]}"
    assert receipt["status"] == "passed" and receipt["turns"] == TURNS
    assert receipt["resume_verified"] and receipt["encrypted_state_verified"]
    assert summary_requests and receipt["archived_rows"] > 0
    assert len(summary_requests) <= TURNS * 3, "summary retry storm"
    assert len(main_requests) <= TURNS * 3, "main request retry storm"
    if inject_faults:
        # The 429 retry keeps the same cached conversation, not a reset or lossy rewrite.
        key = "input" if mode == "codex_responses" else "messages"
        assert main_requests[0]["body"][key] == main_requests[1]["body"][key]
    system_key = "instructions" if mode == "codex_responses" else "system"
    if mode == "chat_completions":
        systems = [r["body"]["messages"][0]["content"] for r in main_requests]
    else:
        systems = [r["body"].get(system_key) for r in main_requests]
    assert systems[0] and all(system == systems[0] for system in systems), "system prefix changed mid-conversation"
