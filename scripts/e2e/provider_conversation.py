"""Bounded real-provider conversation, compaction, and encrypted-resume acceptance.

Credentials enter on stdin. The same probe runs against strict HTTP fixtures in
CI and live endpoints on demand; receipts distinguish those two kinds of proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

TURNS = 8
THRESHOLD = 12_000
TOKEN_BUDGET = 400_000


def _require(condition, message):
    if not condition:
        raise RuntimeError(message)


def _assert_pairs(messages):
    pending = set()
    seen = set()
    for message in messages:
        if message.get("role") == "assistant":
            _require(not pending, "assistant reply preceded its tool results")
            for call in message.get("tool_calls") or []:
                identity = call["id"]
                _require(identity not in seen, "duplicate completed tool call")
                seen.add(identity)
                pending.add(identity)
        elif message.get("role") == "tool":
            identity = message.get("tool_call_id")
            _require(identity in pending, "orphan or duplicate tool result")
            pending.remove(identity)
        elif message.get("role") == "user":
            _require(not pending, "user message preceded outstanding tool results")
    _require(not pending, "conversation ended with unanswered tool calls")


def _config(home, workspace, route, summary_route):
    from hermes_cli.config import save_config, save_env_value
    from hermes_security import vault

    password = secrets.token_urlsafe(32)
    vault.init_vault(home, password)
    vault.unlock(home, password)
    save_env_value("E2E_CONVERSATION_KEY", route["api_key"])
    config = {
        "model": {"provider": route["provider"], "default": route["model"],
                  "base_url": route["base_url"], "context_length": 64_000},
        "providers": {"acceptance-summary": {
            "base_url": summary_route["base_url"], "key_env": "E2E_SUMMARY_KEY",
            "api_mode": summary_route["api_mode"],
        }},
        "compression": {"threshold_tokens": THRESHOLD, "protect_last_n": 4, "protect_first_n": 2},
        "auxiliary": {"title_generation": {"enabled": False}, "compression": {
            "provider": "custom:acceptance-summary", "model": summary_route["model"],
        }},
        "agent": {"api_max_retries": 2, "run_budget_seconds": 180},
        "terminal": {"cwd": str(workspace)}, "curator": {"enabled": False},
        "tools": {"tool_search": {"enabled": "off"}}, "updates": {"check": False},
    }
    save_env_value("E2E_SUMMARY_KEY", summary_route["api_key"])
    save_config(config, merge_existing=False)


def _agent(route, database, session_id):
    from run_agent import AIAgent

    return AIAgent(
        provider=route["provider"], api_mode=route["api_mode"], model=route["model"],
        base_url=route["base_url"], api_key=route["api_key"],
        session_db=database, session_id=session_id, quiet_mode=True, platform="cli",
        enabled_toolsets=["file"], skip_memory=True, skip_context_files=True,
        skip_background_review=True, max_iterations=5, max_tokens=1024,
        reasoning_config={"enabled": False},
    )


def _turn(agent, prompt, history, expected):
    started = time.monotonic()
    result = agent.run_conversation(prompt, conversation_history=history)
    _require(not result.get("failed"), "provider conversation failed")
    _require(expected in (result.get("final_response") or ""), "conversation did not return its retained marker")
    messages = result["messages"]
    _assert_pairs(messages)
    tokens = int(agent.session_input_tokens or 0) + int(agent.session_output_tokens or 0)
    _require(tokens <= TOKEN_BUDGET, "conversation exceeded the token budget")
    _require(time.monotonic() - started < 180, "conversation exceeded the per-turn deadline")
    return messages


def run_probe(root: Path, route: dict, *, summary_route: dict | None = None) -> dict:
    """Use only a newly created home; no existing installation or user state is accepted."""
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from agent.secret_scope import build_profile_secret_scope, reset_secret_scope, set_secret_scope
    from hermes_security import vault
    from hermes_state import SessionDB

    home, workspace = root / "state", root / "workspace"
    home.mkdir(mode=0o700)
    workspace.mkdir(mode=0o700)
    home_token = set_hermes_home_override(home)
    scope_token = None
    agent = database = None
    started = time.monotonic()
    anchor = "CONTINUITY-" + secrets.token_hex(16)
    session_id = "provider-acceptance-" + secrets.token_hex(8)
    turn_markers = []
    try:
        _config(home, workspace, route, summary_route or route)
        scope_token = set_secret_scope(build_profile_secret_scope(home), profile_home=str(home))
        database = SessionDB(home / "state.db")
        agent = _agent(route, database, session_id)
        history = None
        for turn in range(TURNS):
            nonce = "VALUE-" + secrets.token_hex(16)
            reference = workspace / f"reference-{turn}.txt"
            rows = [f"{i:04d} amber quartz lantern river meadow cedar coral horizon {turn} {i * 17}"
                    for i in range(320)]
            reference.write_text("\n".join(rows) + "\nRESULT=" + nonce + "\n", encoding="utf-8")
            marker = f"REFERENCE-TURN-{turn}"
            prompt = (f"{marker}. Read the entire file {reference} using read_file, then reply only with "
                      "the RESULT value from its final line. Do not skip the read or fabricate a value.")
            if turn == 0:
                prompt += (f" Continuity anchor: {anchor}. Remember this exact anchor throughout this "
                           "conversation and preserve it during summaries; I will ask for it after a restart.")
            history = _turn(agent, prompt, history, nonce)
            turn_markers.append(marker)
        compressed = database._conn.execute(
            "SELECT count(*) FROM messages WHERE session_id=? AND compacted=1", (session_id,)).fetchone()[0]
        _require(compressed > 0, "growing conversation never committed a compaction")
        retained = database.get_messages_as_conversation(session_id, include_compacted=True)
        for marker in turn_markers:
            _require(marker in json.dumps(retained), "a user turn disappeared from durable history")
        input_tokens, output_tokens = int(agent.session_input_tokens or 0), int(agent.session_output_tokens or 0)
        calls = int(agent.session_api_calls or 0)
        agent.close()
        agent = _agent(route, database, session_id)
        history = database.get_messages_as_conversation(session_id, repair_alternation=True)
        _turn(agent, "Recall continuity anchor: return only the exact CONTINUITY- anchor from earlier messages. "
                    "Do not use tools or files.", history, anchor)
        _require(input_tokens + output_tokens + int(agent.session_input_tokens or 0)
                 + int(agent.session_output_tokens or 0) <= TOKEN_BUDGET, "resumed conversation exceeded the token budget")
        _require(database._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
                 "encrypted session database failed integrity_check")
        _require(not (home / "state.db").read_bytes().startswith(b"SQLite format 3"), "session database is plaintext")
        _require((home / "config.yaml").read_bytes().startswith(b"HRMVAULT\x00"), "config is plaintext")
        return {"status": "passed", "provider": route["provider"], "api_mode": route["api_mode"],
                "model": route["model"], "endpoint_host": urlsplit(route["base_url"]).hostname,
                "turns": TURNS, "archived_rows": compressed, "resume_verified": True,
                "encrypted_state_verified": True, "input_tokens": input_tokens + int(agent.session_input_tokens or 0),
                "output_tokens": output_tokens + int(agent.session_output_tokens or 0),
                "api_calls": calls + int(agent.session_api_calls or 0), "seconds": round(time.monotonic() - started, 2)}
    finally:
        if agent is not None:
            agent.close()
        if database is not None:
            database.close()
        if scope_token is not None:
            reset_secret_scope(scope_token)
        vault.lock_now(home)
        reset_hermes_home_override(home_token)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    credential = json.load(sys.stdin)
    _require(all(credential.get(key) for key in ("provider", "model", "base_url", "api_key", "api_mode")),
             "complete dedicated live credentials on stdin are required")
    args.evidence.mkdir(parents=True, exist_ok=False)
    receipt = {"status": "failed", "kind": "live", "cleanup_verified": False}
    try:
        with tempfile.TemporaryDirectory(prefix="hermes-provider-acceptance-") as directory:
            receipt.update(run_probe(Path(directory), credential))
        receipt["cleanup_verified"] = True
        receipt["candidate"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, timeout=15).strip()
        receipt["harness_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        from scripts.e2e.state_probe import host_facts
        receipt["host"] = host_facts()
    finally:
        (args.evidence / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
