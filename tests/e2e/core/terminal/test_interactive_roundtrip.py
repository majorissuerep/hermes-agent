"""Real native stdio clarify/approval round trips and pending cards across session switches.

Only HTTP is scripted. The actual runtime, AIAgent, manual approval registry and terminal
remain in the path. Removed remote second-window/reconnect obligations have no local shim.
"""
from __future__ import annotations

import json
import re
import threading

import pytest

from tests.e2e.core.chaos._helpers import hermetic_env
from tests.e2e.core.chaos._tui_rpc import TuiRuntimeProcess, gateway_cwd
from tests.e2e.core.terminal._pty import poll
from tests.e2e.vault_home import write_encrypted_home
from tests.fakes.fake_llm_provider import FakeLLMServer, Text, ToolCall

pytestmark = [pytest.mark.platforms("linux")]
PROMPT_TIMEOUT = 90.0
TAG_RE = re.compile(r"\b(CLARIFY|RMRF)-([a-z0-9_]+)\b")


class Script:
    def __init__(self):
        self.victims = {}
        self.tool_results = {}
        self.lock = threading.Lock()

    def __call__(self, record):
        messages = record["body"].get("messages") or []
        users = [m for m in messages if m.get("role") == "user"]
        match = TAG_RE.search(str((users[-1] if users else {}).get("content") or ""))
        if not match:
            return Text("no tag")
        kind, tag = match.groups()
        if messages[-1].get("role") == "tool":
            with self.lock:
                self.tool_results.setdefault(tag, []).append(str(messages[-1].get("content") or ""))
            return Text(f"DONE {tag}")
        if kind == "CLARIFY":
            return ToolCall("clarify", {"questions": [{"question": f"Which colour for {tag}?",
                                                       "choices": ["red", "blue"]}]})
        return ToolCall("terminal", {"command": f"rm -rf {self.victims[tag]}"})


@pytest.fixture(scope="module")
def backend(tmp_path_factory):
    root = tmp_path_factory.mktemp("native-interactive")
    home = root / "home"
    hermes_home = home / ".hermes"
    script = Script()
    with FakeLLMServer(script) as llm:
        write_encrypted_home(hermes_home, llm.base_url, extra_config=(
            "approvals:\n  mode: manual\n  timeout: 120\n"
            "clarify:\n  timeout: 120\nterminal:\n  backend: local\n"))
        env = hermetic_env(home, hermes_home, "native-interactive")
        env["HERMES_STATE_DB_GUARD_BYPASS"] = "1"
        env["TERMINAL_ENV"] = "local"
        runtime = TuiRuntimeProcess(env, gateway_cwd(root), root / "runtime.stderr")
        try:
            assert runtime.wait_event(lambda e: e.get("type") == "runtime.ready", timeout=60)
            yield root, runtime, script
            assert runtime.close_stdin_and_wait(30) == 0, runtime.tail()
        finally:
            runtime.kill()


def _pending(runtime, sid):
    return runtime.call("approval.pending", {"session_id": sid}).get("approvals") or []


def _assert_tool_result(script, tag, kind, answer, victim):
    results = script.tool_results.get(tag) or []
    assert len(results) == 1, results
    result = results[0]
    if kind == "clarify":
        assert answer in result, result
    elif kind == "approval_deny":
        assert victim.exists() and (victim / "keep.txt").exists(), result
        assert "denied by user" in result, result
    else:
        assert not victim.exists(), result
        payload = json.loads(result)
        assert payload.get("exit_code") == 0, payload
        assert "approved by the user" in str(payload.get("approval")), payload


def _drive(root, runtime, script, kind, how):
    tag = f"{kind}_{how}"
    victim = root / f"victim-{tag}"
    victim.mkdir()
    (victim / "keep.txt").write_text("precious")
    script.victims[tag] = str(victim)
    created = runtime.call("session.create", {"cols": 100, "source": "tui"})
    sid, key = created["session_id"], created["stored_session_id"]
    word = "CLARIFY" if kind == "clarify" else "RMRF"
    submitted = runtime.call("prompt.submit", {"session_id": sid, "text": f"{word}-{tag} please"})
    assert submitted.get("status") == "streaming", submitted
    method = "clarify" if kind == "clarify" else "approval"
    request = runtime.wait_request(method, sid, PROMPT_TIMEOUT)
    rid = request["id"]
    assert isinstance(rid, str) and rid.startswith("srq-"), request
    if kind == "clarify":
        assert request["params"]["questions"][0]["question"] == f"Which colour for {tag}?"
    else:
        assert str(victim) in str(request["params"].get("command")), request
        assert [p.get("request_id") for p in _pending(runtime, sid)] == [request["params"]["request_id"]]
    assert victim.exists(), "Dangerous command ran before approval"
    if how == "switch":
        other = runtime.call("session.create", {"cols": 100, "source": "tui"})
        assert other["session_id"] != sid
        back = runtime.call("session.resume", {"session_id": key, "cols": 100})
        assert back["session_id"] == sid, back
        assert [r["id"] for r in back.get("open_requests", []) if r.get("method") == method] == [rid], back
    answer = f"blue-{tag}"
    if kind == "clarify":
        runtime.respond(rid, {"answers": {"q0": answer}})
    elif how == "rpc":
        resolved = runtime.call("approval.respond", {"session_id": sid,
            "choice": "deny" if kind == "approval_deny" else "once",
            "request_id": request["params"]["request_id"]})
        assert resolved.get("resolved"), resolved
    else:
        runtime.respond(rid, {"choice": "deny" if kind == "approval_deny" else "once"})
    done = runtime.wait_event(lambda e: e.get("type") == "message.complete"
                             and e.get("session_id") == sid, timeout=PROMPT_TIMEOUT)
    assert done and f"DONE {tag}" in str(done.get("payload", {}).get("text")), done
    _assert_tool_result(script, tag, kind, answer, victim)
    poll(lambda: not _pending(runtime, sid), timeout=30, what="approval cleared")
    final = runtime.call("session.resume", {"session_id": key, "cols": 100})
    assert not final.get("open_requests") and not final.get("pending_approval"), final
    requests = [r for r in runtime.server_requests if r.get("params", {}).get("session_id") == sid]
    assert [r["id"] for r in requests] == [rid], requests
    runtime.call("session.close", {"session_id": sid})


@pytest.mark.parametrize(("kind", "how"), [
    ("clarify", "immediate"), ("clarify", "switch"),
    ("approval_deny", "immediate"), ("approval_deny", "rpc"),
    ("approval_approve", "immediate"), ("approval_approve", "switch"),
])
def test_interactive_roundtrip(backend, kind, how):
    root, runtime, script = backend
    _drive(root, runtime, script, kind, how)
