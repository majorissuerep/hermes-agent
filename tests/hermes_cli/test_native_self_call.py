"""A native owner can run a full encrypted Hermes child and keep using its vault."""
import json
import os
import shlex

import pytest

from tests.e2e.core.mcp_plugins._helpers import build_home, payload, provider, run_chat_q
from tests.e2e.vault_home import connect_state, prepare_fixture_cli
from tests.fakes.fake_llm_provider import MODEL_ID, Text, ToolCall

pytestmark = pytest.mark.platforms("posix")


def test_native_owner_calls_full_managed_chat_child_then_reads_its_own_state(tmp_path):
    from pathlib import Path
    from agent.redact import redact_sensitive_text

    root = Path(__file__).resolve().parents[2]
    query = tmp_path / "inner-query.txt"
    query.write_text("INNER_NATIVE_CHILD: read the owning configuration.", encoding="utf-8")
    observed = {"inner_read": False, "child_completed": False, "parent_read": False}
    url = ""

    def respond(record):
        messages = record["body"].get("messages", [])
        users = "\n".join(str(m.get("content", "")) for m in messages if m["role"] == "user")
        results = [payload(str(m["content"])) for m in messages if m["role"] == "tool"]
        read = {"command": "hermes config get agent.max_turns", "timeout": 30}
        if "INNER_NATIVE_CHILD" in users:
            if not results:
                return ToolCall("terminal", read)
            observed["inner_read"] = results[-1].get("exit_code") == 0 and str(results[-1].get("output", "")).strip() == "69"
            return Text("INNER_NATIVE_DONE" if observed["inner_read"] else "INNER_NATIVE_FAILED")
        if "OUTER_NATIVE_OWNER" not in users:
            return Text("auxiliary")
        if not results:
            command = ["hermes", "manage", "run", "hermes", "chat", "-Q", "--query-file", str(query),
                       "--provider", "custom", "--model", MODEL_ID, "--base-url", url,
                       "--toolsets", "terminal", "--ignore-rules", "--max-turns", "5"]
            return ToolCall("terminal", {"command": shlex.join(command), "timeout": 90})
        if len(results) == 1:
            observed["child_completed"] = results[-1].get("exit_code") == 0 and "INNER_NATIVE_DONE" in str(results[-1].get("output", ""))
            return ToolCall("terminal", read)
        observed["parent_read"] = results[-1].get("exit_code") == 0 and str(results[-1].get("output", "")).strip() == "69"
        return Text("NESTED_OWNER_VERIFIED" if all(observed.values()) else "NESTED_OWNER_FAILED")

    with provider(respond) as server:
        url = server.base_url
        home = build_home(tmp_path, url, extra={
            "agent": {"max_turns": 69, "api_max_retries": 1}, "terminal": {"env": "local"},
            "memory": {"memory_enabled": False, "user_profile_enabled": False},
            "auxiliary": {"title_generation": {"enabled": False}},
        })
        home.update_config(lambda cfg: cfg["model"].update(api_mode="chat_completions"))
        if runtime := os.environ.get("HERMES_RUNTIME_DIR"):
            home.extra_env["HERMES_RUNTIME_DIR"] = runtime
        directory = prepare_fixture_cli(home.hermes_home, root, tmp_path / "fixture-cli")
        home.extra_env["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
        result = run_chat_q(home, "OUTER_NATIVE_OWNER: run the child and verify your own state afterward.", timeout=150)
        results = [redact_sensitive_text(str(m.get("content", "")), force=True) for r in server.main_requests()
                   for m in r.get("messages", []) if m.get("role") == "tool"]
        diagnostic = (observed, result.stdout, result.stderr, results)
        assert result.returncode == 0 and "NESTED_OWNER_VERIFIED" in result.stdout, diagnostic
        assert all(observed.values()), diagnostic
        assert len(server.main_requests()) == 5
        with connect_state(home.hermes_home / "state.db") as database:
            assert database.execute("SELECT count(*) FROM sessions").fetchone()[0] == 2
            rows = database.execute("SELECT content FROM messages WHERE role='tool'").fetchall()
            saved = [json.loads(row[0]) for row in rows]
            assert sum(row.get("exit_code") == 0 and str(row.get("output", "")).strip() == "69" for row in saved) == 2
        assert (home.hermes_home / "config.yaml").read_bytes().startswith(b"HRMVAULT")
        assert not (home.hermes_home / "state.db").read_bytes().startswith(b"SQLite format 3")
