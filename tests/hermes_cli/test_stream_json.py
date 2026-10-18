"""``hermes chat -q … --format stream-json`` emits a parseable JSONL event stream and nothing else on stdout."""

import json

import pytest

from hermes_cli.stream_json import StreamJsonEmitter


def _events(capsys):
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line]


def test_emitter_event_stream_is_valid_jsonl(capsys):
    emitter = StreamJsonEmitter(model="test-model", session_id="s-1")
    emitter.on_text_delta("hel")
    emitter.on_text_delta("\n  ")  # whitespace deltas are part of the answer and must be forwarded verbatim
    emitter.on_text_delta("lo")
    emitter.on_text_delta(None)  # the turn-end sentinel the agent sends
    emitter.on_text_delta("")
    emitter.on_tool_progress("tool.started", "read_file", "preview", {"path": "x"}, tool_call_id="call-a")
    emitter.on_tool_progress("tool.started", "read_file", "preview", {"path": "y"}, tool_call_id="call-b")
    emitter.on_tool_progress("reasoning.available", "_thinking", "hmm", None)  # not part of the protocol
    emitter.on_tool_progress("tool.completed", "read_file", None, None, tool_call_id="call-b", result="y")
    emitter.on_tool_progress("tool.completed", "read_file", None, None, tool_call_id="call-a", duration=0.5,
                             is_error=False, result="x" * 6000)
    code = emitter.emit_result({"final_response": "", "failed": True, "error": "boom", "input_tokens": 3}, exit_code=0)

    events = _events(capsys)
    assert [e["type"] for e in events] == ["system", "text", "text", "text", "tool_use", "tool_use", "tool_result",
                                           "tool_result", "result"]
    assert "".join(e["text"] for e in events if e["type"] == "text") == "hel\n  lo"
    assert events[0]["subtype"] == "init" and events[0]["model"] == "test-model"
    assert events[4]["input"] == {"path": "x"} and events[4]["tool_call_id"] == "call-a"
    # concurrent same-name calls: each result pairs with its own start, not the last-started one
    assert [e["tool_call_id"] for e in events if e["type"] == "tool_result"] == ["call-b", "call-a"]
    assert events[6]["duration_ms"] < 500
    assert events[7]["duration_ms"] == 500 and events[7]["output"].endswith("...") and len(events[7]["output"]) == 5003
    assert code == 1 and events[-1] == {**events[-1], "exit_code": 1, "error": "boom", "session_id": "s-1"}
    assert events[-1]["tokens"]["input"] == 3
    assert all("timestamp" in e for e in events)


def _run_stream_json_chat(monkeypatch, capsys, run_turn, credentials_ok=True):
    """Real native oneshot output boundary with deterministic agent callbacks."""
    from hermes_cli import oneshot

    def run_agent(*args, launch_options, **kwargs):
        emitter = launch_options["emitter"]
        if not credentials_ok:
            raise ValueError("missing credentials")
        emitter.bind_session("session-123")

        class Peer:
            def emit(self, kind, payload):
                if kind == "message.delta":
                    emitter.on_text_delta(payload["text"])
                elif kind == "tool.start":
                    emitter.on_tool_progress("tool.started", payload["tool_name"],
                                             args=payload["args"], tool_call_id=payload["tool_call_id"])
                elif kind == "tool.complete":
                    emitter.on_tool_progress("tool.completed", payload["tool_name"],
                                             preview=payload["result"], tool_call_id=payload["tool_call_id"])

        result = run_turn(Peer())
        return result["text"], {"final_response": result["text"], "completed": True,
                                "session_id": "session-123"}

    monkeypatch.setattr(oneshot, "_run_agent", run_agent)
    code = oneshot.run_oneshot("hello", model="test-model", launch_options={"output_format": "stream-json"})
    return code, _events(capsys)


def _ok_turn(peer):
    peer.emit("message.delta", {"text": "hello"})
    peer.emit("tool.start", {"tool_call_id": "c1", "tool_name": "read_file", "args": {"path": "f"}})
    peer.emit("tool.complete", {"tool_call_id": "c1", "tool_name": "read_file", "args": {"path": "f"},
                                "is_error": False, "result": "contents"})
    return {"text": "hello", "outcome": "completed"}


def _interrupted_turn(_peer):
    raise KeyboardInterrupt


@pytest.mark.parametrize("turn, credentials_ok, exit_code, types", [
    (_ok_turn, True, 0, ["system", "text", "tool_use", "tool_result", "result"]),
    (_interrupted_turn, True, 130, ["system", "result"]),
    (_ok_turn, False, 1, ["system", "result"]),  # credentials fail before any session exists
])
def test_chat_stream_json_implies_quiet_and_closes_with_result(monkeypatch, capsys, turn, credentials_ok, exit_code,
                                                                types):
    """No ``-Q`` needed; stdout is only JSONL; the stream always ends in a ``result`` carrying the exit code."""
    code, events = _run_stream_json_chat(monkeypatch, capsys, turn, credentials_ok=credentials_ok)
    assert code == exit_code
    assert [e["type"] for e in events] == types
    assert events[-1]["exit_code"] == exit_code
    # A launch refused before the gateway created a session has none to report.
    assert events[-1]["session_id"] == ("session-123" if credentials_ok else "")


@pytest.mark.parametrize("argv, message", [
    (["chat", "--format", "stream-json"], "requires -q/--query"),
    (["chat", "-q", "hi", "--format", "stream-json", "--tui"], "cannot be used with --tui"),
    (["--tui", "chat", "-q", "hi", "--format", "stream-json"], "cannot be used with --tui"),
])
def test_chat_stream_json_rejects_interactive_combinations(monkeypatch, capsys, argv, message):
    import hermes_cli.main as cli_entry
    from hermes_cli._parser import build_top_level_parser

    monkeypatch.setattr(cli_entry, "_launch_tui", lambda *_a, **_k: pytest.fail("TUI launched"))
    parser, _, _ = build_top_level_parser()
    with pytest.raises(SystemExit) as exc_info:
        cli_entry.cmd_chat(parser.parse_args(argv))
    assert exc_info.value.code == 2
    assert message in capsys.readouterr().err
