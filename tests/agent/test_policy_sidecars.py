"""Real provider wires, tool dispatch and A→B→A profile isolation for sidecars."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest


@pytest.fixture
def endpoint(monkeypatch):
    from agent.auxiliary_client import shutdown_cached_clients
    shutdown_cached_clients()
    state = {"requests": [], "guard": "approve", "complexity": 1, "remaining_calls": 1,
             "output_tokens": 128, "cache_fraction": None, "broken": False, "main": []}
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_json({"data": [{"id": name, "context_length": 131072} for name in ("main", "fast", "strong")]})

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append((request, self.headers.get("Authorization")))
            model = request["model"]
            if model.startswith("guard"):
                content = "invalid" if state["broken"] else json.dumps({"action": state["guard"], "reason": "Policy decision"})
            elif model.startswith("router"):
                content = "invalid" if state["broken"] else json.dumps({
                    "complexity": state["complexity"], "remaining_calls": state["remaining_calls"],
                    "output_tokens": state["output_tokens"], "reason": "Remaining task complexity",})
            else:
                state["main"].append(request)
                content = "Task complete."
            response = {"id": "sidecar-test", "created": 1, "object": "chat.completion", "model": model,
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 1000, "completion_tokens": 128, "total_tokens": 1128}}
            if state["cache_fraction"] is not None and model in {"main", "fast", "strong"}:
                from agent.model_metadata import estimate_request_tokens_rough
                prompt = estimate_request_tokens_rough(request["messages"], tools=request.get("tools"))
                response["usage"] = {"prompt_tokens": prompt, "completion_tokens": 128, "total_tokens": prompt + 128,
                                     "prompt_tokens_details": {"cached_tokens": int(prompt * state["cache_fraction"])}}
            if request.get("stream"):
                response["object"] = "chat.completion.chunk"
                response["choices"][0]["delta"] = response["choices"][0].pop("message")
                body = f"data: {json.dumps(response)}\n\ndata: [DONE]\n\n".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_json(response)

        def send_json(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", state
    finally:
        shutdown_cached_clients()
        server.shutdown()
        server.server_close()
        thread.join(5)


def _config(base, suffix="", routing=False):
    return {
        "model": {"provider": "custom", "default": "main", "base_url": base, "context_length": 131072},
        "compression": {"enabled": False}, "display": {"streaming": False},
        "terminal": {"env_type": "local", "cwd": "/workspace"},
        "auxiliary": {task: {"provider": "custom", "model": model + suffix, "base_url": base, "api_key": "${SIDECAR_KEY}", "timeout": 5}
                      for task, model in (("tool_guard", "guard"), ("model_router", "router"))},
        "approvals": {"mode": "off", "unattended_mode": "approve", "guard": {"enabled": not routing}},
        "smart_model_routing": {"enabled": routing, "models": [
            {"name": name, "provider": "custom", "model": name, "base_url": base,
             "tier": tier, "context_length": 131072, "prices": {"input": price, "output": price * 2, "cache_read": price / 100}}
            for name, tier, price in (("main", 2, 5), ("fast", 1, 1), ("strong", 3, 10))]},
        "custom_providers": [{"name": "test", "base_url": base, "api_key": "${SIDECAR_KEY}", "context_length": 131072}],
    }


def _save(home, config):
    from hermes_cli.config import atomic_config_write
    home.mkdir(parents=True, exist_ok=True)
    atomic_config_write(home / "config.yaml", config)


def test_guard_blocks_all_dispatch_paths_and_preserves_the_human_approval_boundary(tmp_path, monkeypatch, endpoint):
    from hermes_cli.config import load_config_readonly
    from agent.policy_sidecars import PolicySidecars
    from agent.tool_guardrails import ToolCallGuardrailController
    from agent.tool_executor import _dispatch_authorized_once, _ManagedToolResult, _ToolCallRef
    from agent.tool_policy import policy_scope
    import model_tools
    from tools.registry import registry
    from tools.approval_context import set_hermes_interactive_context, reset_hermes_interactive_context

    base, state = endpoint
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("SIDECAR_KEY", "profile-test-key")
    _save(tmp_path, _config(base))
    sidecars = PolicySidecars(load_config_readonly())
    task = "Inspect README.md inside /workspace and report its title."
    sidecars.guard.update_task(task, [{"role": "user", "content": task}])
    executions = []
    name = "sidecar_test_write"
    registry.register(name, "sidecar_test", {"name": name, "description": "test", "parameters": {"type": "object"}},
                      lambda args, **kwargs: executions.append(args) or json.dumps({"ok": True}))
    agent = SimpleNamespace(_policy_sidecars=sidecars, _tool_guardrails=ToolCallGuardrailController(), session_id="test")
    monkeypatch.setattr("agent.tool_executor._pre_tool_block", lambda _agent, ref: (None, ref.args))
    monkeypatch.setattr("agent.tool_executor._begin_tool_execution", lambda *_args: None)
    monkeypatch.setattr("agent.tool_executor._emit_terminal_post_tool_call", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("agent.tool_executor._run_with_activity_heartbeat", lambda _agent, _name, fn: fn())
    ref = _ToolCallRef(name, {"path": "/workspace/report.txt"}, "test", "call", [])

    def dispatch():
        return model_tools.handle_function_call(name, ref.args, "test", enabled_tools=[name], skip_pre_tool_call_hook=True)

    for verdict in ("approve", "deny", "escalate"):
        state["guard"] = verdict
        managed = _ManagedToolResult(result=None, args=ref.args, middleware_trace=[], blocked=False, dispatched=False)
        result = _dispatch_authorized_once(agent, managed, ref, execute=lambda _args: dispatch(), scope_block=None,
                                           display_index=None, begin_execution=None, authorization_gate=None)
        if verdict == "approve":
            assert not managed.blocked and json.loads(result)["ok"]
        else:
            assert managed.blocked and json.loads(result)["tool_policy"]["action"] == verdict
    assert executions == [ref.args]
    # A nested RPC does not inherit the root's approval. The exact args are reviewed again
    # after the one-use root permit has been consumed, including middleware rewrites.
    state["guard"] = "deny"
    with policy_scope(sidecars.guard, reviewed_name=name, reviewed_args=ref.args):
        dispatch()
        denied = json.loads(dispatch())
        assert denied["tool_policy"]["executed"] is False
    assert len(executions) == 2
    # A profile/session that disables its guard must not inherit another agent's
    # active binding. Returning from it restores the original guard.
    with policy_scope(sidecars.guard):
        with policy_scope(None):
            assert json.loads(dispatch())["ok"] is True
        assert json.loads(dispatch())["tool_policy"]["action"] == "deny"
    assert len(executions) == 3
    # The actual persistent-kernel authority captures this cell's policy, and its
    # authenticated socket transport reviews an inner tool on the serving thread.
    import socket
    from tools.code_kernel import CellAuthority
    from tools.code_execution_rpc import _serve_rpc_connection
    with policy_scope(sidecars.guard):
        authority = CellAuthority("test")
    host_socket, client_socket = socket.socketpair()
    thread = threading.Thread(target=_serve_rpc_connection, args=(host_socket, "test", [], [0], 10,
                              frozenset({name}), "test-token"), kwargs={"dispatch": authority.dispatch})
    thread.start()
    try:
        client_socket.settimeout(5)
        client_socket.sendall((json.dumps({"token": "test-token", "tool": name, "args": ref.args}) + "\n").encode())
        with client_socket.makefile("rb") as stream:
            response = stream.readline()
        assert json.loads(response)["tool_policy"]["action"] == "deny"
    finally:
        client_socket.close()
        thread.join(5)
        authority.retire()
    assert not thread.is_alive() and len(executions) == 3
    # Direct AIAgent invocation is a second public entry point, independent of
    # the managed executor. It must refuse the same call before the handler runs.
    from run_agent import AIAgent
    direct_agent = AIAgent(model="main", provider="custom", base_url=base, api_key="profile-test-key",
                           enabled_toolsets=[], quiet_mode=True, skip_memory=True, skip_context_files=True,
                           save_trajectories=False, cwd=str(tmp_path / "session-workspace"))
    direct_agent.valid_tool_names = {name}
    direct = direct_agent._invoke_tool(name, ref.args, "test", messages=[{"role": "user", "content": task}])
    assert json.loads(direct)["tool_policy"]["action"] == "deny"
    assert len(executions) == 3
    assert direct_agent._policy_sidecars.guard.workspace == direct_agent.session_cwd
    # Inline tools consume the outer permit too, even when a nested call uses
    # the identical name and arguments rather than a different tool.
    import agent.inline_tool_executors as inline
    def nested_inline(_agent, _args, _ctx):
        state["guard"] = "deny"
        return dispatch()
    monkeypatch.setitem(inline.INLINE_TOOL_EXECUTORS, name, nested_inline)
    state["guard"] = "approve"
    nested = direct_agent._invoke_tool(name, ref.args, "test", messages=[{"role": "user", "content": task}])
    assert json.loads(nested)["tool_policy"]["action"] == "deny" and len(executions) == 3
    state["broken"] = True
    assert json.loads(sidecars.guard.review(name, ref.args))["tool_policy"]["action"] == "escalate"
    sent = len(state["requests"])
    assert json.loads(sidecars.guard.review(name, {"content": "x" * 12000}))["tool_policy"]["action"] == "escalate"
    assert len(state["requests"]) == sent  # No truncated operation or fallback request.
    # A real interactive callback is still invoked with mode=off; a deny never asks it.
    state["broken"] = False
    state["guard"] = "escalate"
    asked = []
    token = set_hermes_interactive_context(True)
    try:
        assert sidecars.guard.review(name, ref.args, lambda *args, **kwargs: asked.append((args, kwargs)) or "once") is None
        callback = lambda *args, **kwargs: asked.append((args, kwargs)) or "session"
        assert sidecars.guard.review(name, ref.args, callback) is None
        confirmed = len(asked)
        assert sidecars.guard.review(name, ref.args, callback) is None
        assert len(asked) == confirmed  # Honor explicit approval of this exact scoped operation.
        sidecars.guard.update_task("A different authorized task", [])
        assert sidecars.guard.review(name, ref.args, callback) is None
        assert len(asked) == confirmed + 1
        state["guard"] = "deny"
        assert json.loads(sidecars.guard.review(name, ref.args, callback))["tool_policy"]["action"] == "deny"
        assert len(asked) == confirmed + 1
    finally:
        reset_hermes_interactive_context(token)
    assert asked
    assert all(request["model"] == "guard" for request, _key in state["requests"])
    packet = json.loads(state["requests"][0][0]["messages"][-1]["content"])
    assert packet["authorized_task"] == task and packet["proposed_call"]["arguments"] == ref.args
    assert packet["tool_contract"] == registry.get_schema(name)


def test_profiles_and_real_turns_keep_policy_cache_and_route_state_separate(tmp_path, monkeypatch, endpoint):
    from gateway.run import _profile_runtime_scope
    from agent.secret_scope import is_multiplex_active, set_multiplex_active
    from agent.policy_sidecars import get_sidecars
    from run_agent import AIAgent

    base, state = endpoint
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    homes = [tmp_path / "A", tmp_path / "B"]
    monkeypatch.setenv("HERMES_HOME", str(homes[0]))
    for home, label in zip(homes, ("A", "B")):
        _save(home, _config(base, suffix=label, routing=True))
        (home / ".env").write_text(f"SIDECAR_KEY=key-{label}\n")
    prior = is_multiplex_active()
    set_multiplex_active(True)
    agents = []
    try:
        for index in (0, 1, 0):
            with _profile_runtime_scope(homes[index]):
                agent = AIAgent(model="main", provider="custom", base_url=base, api_key=f"key-{'AB'[index]}",
                                enabled_toolsets=[], quiet_mode=True, skip_memory=True, skip_context_files=True,
                                save_trajectories=False, max_iterations=3)
                agent._cached_system_prompt = "Stable system instructions."
                agent._disable_streaming = True
                agent.tool_delay = 0
                original_tools = json.dumps(agent.tools, sort_keys=True)
                result = agent.run_conversation("Read README.md and report its title.")
                assert result["final_response"] == "Task complete."
                assert agent.model == "fast"
                assert agent._cached_system_prompt == "Stable system instructions."
                assert json.dumps(agent.tools, sort_keys=True) == original_tools
                sidecars = get_sidecars(agent)
                assert sidecars.router.last_decision["action"] == "switch"
                # Repeat failures raise capability even when the auxiliary assessment says simple.
                sidecars.router.note_tool_result("read_file", {"path": "README.md"}, "error", True)
                sidecars.router.note_tool_result("read_file", {"path": "README.md"}, "error", True)
                sidecars.router.prepare(agent, "Diagnose the repeated failure", result["messages"])
                assert sidecars.router.last_decision["required_tier"] > 1
                assert agent.model == "main"
                agents.append(agent)
    finally:
        set_multiplex_active(prior)
    requests = [(req["model"], auth) for req, auth in state["requests"] if req["model"].startswith("router")]
    assert requests == [("routerA", "Bearer key-A"), ("routerA", "Bearer key-A"),
                        ("routerB", "Bearer key-B"), ("routerB", "Bearer key-B"),
                        ("routerA", "Bearer key-A"), ("routerA", "Bearer key-A")]
    assert len({id(agent._policy_sidecars.router) for agent in agents}) == len(agents)
    assert all(request["messages"][0]["content"] == "Stable system instructions." for request in state["main"])
    # A long conversation with a provider-confirmed cache hit should stay for one
    # cheap continuation, yet switch once repeated output savings repay the cold prefix.
    from decimal import Decimal
    state["complexity"] = 2
    state["cache_fraction"] = 0.99
    with _profile_runtime_scope(homes[0]):
        agent = agents[-1]
        history = [{"role": "user", "content": "Inspect the project."},
                   {"role": "assistant", "content": "Earlier findings: " + "a" * 240000}]
        for _ in range(3):
            turn = agent.run_conversation("Report the README title.", conversation_history=history)
            history = turn["messages"]
        state["complexity"] = 1
        router = agent._policy_sidecars.router
        router.prepare(agent, "Report the README title.", history)
        decision = router.last_decision
        assert decision["action"] == "stay" and decision["warm_prefix_tokens"] > 0
        assert Decimal(decision["quotes"]["main"]["next_call_usd"]) < Decimal(decision["quotes"]["fast"]["next_call_usd"])
        warm_model = next(model for model in router.models if model.name == "main")
        prior_key = agent.api_key
        agent.api_key = "rotated-account-key"
        assert router._warm_prefix(agent, history, warm_model) == 0
        agent.api_key = prior_key
        assert router._warm_prefix(agent, history, warm_model) > 0
        state["remaining_calls"], state["output_tokens"] = 8, 4096
        router.prepare(agent, "Continue several routine steps.", history)
        assert router.last_decision["action"] == "switch" and agent.model == "fast"
        assert router.observation is None
        assert agent._cached_system_prompt == "Stable system instructions."
        state["broken"] = True
        router.prepare(agent, "Continue", history)
        assert router.last_decision["action"] == "stay" and agent.model == "fast"
        # The highest tier must not trap a failing model just because it is
        # cheaper than its equally capable peer. Exercise resolution on a real turn.
        state["broken"] = False
        cfg = _config(base, suffix="A", routing=True)
        cfg["smart_model_routing"]["models"][0]["tier"] = 3
        _save(homes[0], cfg)
        peer_agent = AIAgent(model="main", provider="custom", base_url=base, api_key="key-A",
                             enabled_toolsets=[], quiet_mode=True, skip_memory=True, skip_context_files=True,
                             save_trajectories=False)
        peer_router = get_sidecars(peer_agent).router
        peer_router.note_tool_result("read_file", {"path": "missing"}, "error", True)
        peer_router.note_tool_result("read_file", {"path": "missing"}, "error", True)
        peer_agent._cached_system_prompt = "Stable system instructions."
        peer_router.prepare(peer_agent, "Recover from the repeated failures", history)
        assert peer_agent.model == "strong" and peer_router.last_decision["required_tier"] == 3
