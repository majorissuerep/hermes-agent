"""Real state readers/writers used by the isolated encrypted lifecycle rehearsal.

Input travels on stdin, never argv/environment. Only semantic hashes leave this
process. No application dependency is mocked or replaced.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def main() -> None:
    request = json.load(sys.stdin)
    from hermes_constants import get_hermes_home
    from hermes_security import vault, io

    home = get_hermes_home()
    vault.unlock(home, request["password"])
    from agent.secret_scope import build_profile_secret_scope, set_secret_scope, set_multiplex_context
    set_secret_scope(build_profile_secret_scope(home), profile_home=str(home))
    set_multiplex_context(True)
    # Do not cache a degraded config parse by importing readers while locked.
    from hermes_cli.config import read_raw_config, load_config, save_config, save_env_value, load_env
    from hermes_cli.config_defaults import DEFAULT_CONFIG
    from hermes_state import SessionDB
    from cron.jobs import create_job, pause_job, load_jobs

    def seed():
        credential = request["credential"]
        provider = credential["provider"]
        config = {
            "model": {"provider": provider, "default": credential["model"]},
            "agent": {"max_turns": 17, "run_budget_seconds": 240},
            "delegation": {"max_concurrent_children": 2, "max_iterations": 8},
            "terminal": {"backend": "local", "cwd": str(request["workspace"])},
            "curator": {"enabled": False},
            "_config_version": DEFAULT_CONFIG["_config_version"],
        }
        if provider == "openai-codex":
            from hermes_cli.auth_codex import _save_codex_tokens
            _save_codex_tokens(credential["tokens"], set_active=True)
        else:
            save_env_value("E2E_GATEWAY_API_KEY", credential["api_key"])
            config["providers"] = {provider: {
                "base_url": credential["base_url"], "key_env": "E2E_GATEWAY_API_KEY",
                "api_mode": credential.get("api_mode", "chat_completions"),
            }}
        save_env_value("E2E_RETAINED_SECRET", "synthetic-retained-secret")
        from hermes_cli.auth import write_credential_pool

        write_credential_pool("retired-e2e-provider", [{"id": "retained-legacy", "source": "manual",
            "auth_type": "api_key", "access_token": "synthetic-retained-legacy-key",
            "base_url": "https://retired.example.invalid/v1"}])
        # Installation may have seeded another provider's endpoint or inline
        # key. Choose a complete route, not a merge that keeps those fields.
        seeded = read_raw_config()
        seeded.update(config)
        save_config(seeded, merge_existing=False)
        db = SessionDB()
        try:
            db.create_session("e2e-retained", "cli")
            db.append_message("e2e-retained", "user", "Remember this retained marker for later: "
                              + request.get("resume_token", "synthetic-retained-resume-token"))
            db.append_message("e2e-retained", "assistant", "retained assistant message")
        finally:
            db.close()
        io.write_text(home / "memories" / "MEMORY.md", "E2E retained memory", purpose="memory")
        io.write_bytes(home / "attachments" / "e2e.bin", b"E2E retained attachment\x00\xff", purpose="state")
        skill = home / "skills" / "e2e-retained" / "SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text('---\nname: e2e-retained\ndescription: Retain a rehearsal artifact.\n---\n# Retained\n')
        job = create_job(prompt="Retain this paused fixture; never run it.",
                         schedule="2099-01-01T00:00:00Z", name="e2e-retained")
        pause_job(job["id"])
        return manifest()


    def snapshot():
        from hermes_cli.backup import create_quick_snapshot
        snapshot = create_quick_snapshot(label="e2e-restore", hermes_home=home)
        if not snapshot:
            raise RuntimeError("real snapshot creation failed")
        return {"snapshot": snapshot}


    def restore():
        from hermes_cli.backup import restore_quick_snapshot
        save_env_value("E2E_RETAINED_SECRET", "deliberately-mutated")
        save_config({"agent": {"max_turns": 3}}, merge_existing=True)
        if not restore_quick_snapshot(request["snapshot"], hermes_home=home):
            raise RuntimeError("real snapshot restoration failed")
        return manifest()


    def manifest():
        config = read_raw_config()
        db = SessionDB()
        try:
            messages = [(row["role"], row["content"]) for row in db.get_messages("e2e-retained")]
            check = db._conn.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise RuntimeError("encrypted session store failed integrity_check")
        finally:
            db.close()
        files = {
            "memory": io.read_text(home / "memories" / "MEMORY.md", purpose="memory"),
            "attachment": (io.read_bytes(home / "attachments" / "e2e.bin", purpose="state") or b"").hex(),
            "skill": (home / "skills" / "e2e-retained" / "SKILL.md").read_text(),
        }
        if (home / "memories" / "legacy.md").exists():
            files["legacy_memory"] = io.read_text(home / "memories" / "legacy.md", purpose="memory")
            files["legacy_secret"] = load_env().get("E2E_LEGACY_SECRET")
        if not messages or not all(files.values()):
            raise RuntimeError("retained state is incomplete")
        for path in (home / "config.yaml", home / ".env", home / "memories" / "MEMORY.md"):
            if not path.read_bytes().startswith(b"HRMVAULT"):
                raise RuntimeError(f"state was not sealed: {path.name}")
        if (home / "state.db").read_bytes().startswith(b"SQLite format 3"):
            raise RuntimeError("plaintext session database")
        # New default keys are allowed during migration; explicitly seeded values
        # and job identity/semantics must survive, not an old DEFAULT_CONFIG snapshot.
        effective = load_config()
        settings = {section: {key: effective[section][key] for key in keys} for section, keys in {
            "model": ("provider", "default"), "delegation": ("max_concurrent_children", "max_iterations"),
            "terminal": ("backend", "cwd"),
        }.items()}
        from hermes_cli.auth import _load_auth_store, read_credential_pool

        provider = settings["model"]["provider"]
        if provider == "openai-codex":
            credentials = _load_auth_store()["providers"][provider]["tokens"]
        else:
            credentials = {"key": load_env()["E2E_GATEWAY_API_KEY"],
                           "endpoint": config["providers"][provider]}
        from hermes_cli.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider(requested=provider, target_model=settings["model"]["default"])
        selected_key = runtime["api_key"]
        selected_key = selected_key() if callable(selected_key) else selected_key
        expected_key = credentials["access_token"] if provider == "openai-codex" else credentials["key"]
        if not hmac.compare_digest(str(selected_key), str(expected_key)):
            raise RuntimeError("runtime credential differs from the retained credential")
        expected_endpoint = ("https://chatgpt.com/backend-api/codex" if provider == "openai-codex"
                             else credentials["endpoint"]["base_url"])
        if runtime["base_url"].rstrip("/") != expected_endpoint.rstrip("/"):
            raise RuntimeError("runtime endpoint differs from the selected acceptance route")
        jobs = [{key: job.get(key) for key in ("id", "name", "prompt", "schedule", "enabled", "state")}
                for job in load_jobs() if job.get("name") == "e2e-retained"]
        if len(jobs) != 1 or jobs[0]["enabled"] is not False:
            raise RuntimeError("retained paused job is missing or became runnable")
        manifest = {**files, "messages": messages, "jobs": jobs, "config": settings,
                    "max_turns": config["agent"]["max_turns"],
                    "credentials": credentials, "legacy_credentials": read_credential_pool("retired-e2e-provider"),
                    "retained_secret": load_env()["E2E_RETAINED_SECRET"]}
        return {key: digest(value) for key, value in manifest.items()}


    def update_status():
        from hermes_cli.update_receipt import read_latest_receipt
        receipt = read_latest_receipt() or {}
        if (receipt.get("outcome") != "success" or not receipt.get("finished_at")
                or not receipt.get("post_update") or receipt.get("followups")):
            raise RuntimeError("update is not fully settled: " + str(receipt.get("outcome"))
                               + "; followups=" + str([r.get("step") for r in receipt.get("followups", [])]))
        return {"outcome": "success", "followups": [], "update_id": receipt.get("update_id")}

    def live_proof():
        db = SessionDB()
        try:
            calls = db._conn.execute("SELECT COUNT(*) FROM messages WHERE tool_name='delegate_task' "
                                     "OR (role='assistant' AND tool_calls LIKE '%delegate_task%')").fetchone()[0]
            children = db._conn.execute("SELECT COUNT(*) FROM sessions WHERE parent_session_id IS NOT NULL").fetchone()[0]
            child_ids = {row[0] for row in db._conn.execute(
                "SELECT id FROM sessions WHERE parent_session_id IS NOT NULL")}
            replies = {row[0]: row[1] for row in db._conn.execute(
                "SELECT tool_call_id, content FROM messages WHERE role='tool'")}
            executed = {}
            for session_id, raw in db._conn.execute(
                    "SELECT session_id, tool_calls FROM messages WHERE role='assistant' AND tool_calls IS NOT NULL"):
                for call in json.loads(raw):
                    function = call["function"]
                    if function["name"] != "terminal" or call["id"] not in replies:
                        continue
                    args = json.loads(function["arguments"])
                    command = args.get("command", "").strip()
                    if command not in {"hermes config get agent.max_turns", "hermes update --check",
                                       "hermes config set agent.max_turns 23"}:
                        continue
                    result = json.loads(replies[call["id"]])
                    if result.get("exit_code") == 0:
                        executed[command] = session_id in child_ids
        finally:
            db.close()
        complete = bool(calls and children
                and executed.get("hermes config get agent.max_turns") is True
                and executed.get("hermes update --check") is False
                and executed.get("hermes config set agent.max_turns 23") is False
                and read_raw_config()["agent"]["max_turns"] == 23)
        if not complete and not request.get("allow_pending"):
            raise RuntimeError("live task did not successfully execute the required child/parent Hermes calls")
        return {"complete": complete, "delegation_calls": calls, "child_sessions": children,
                "verified_hermes_calls": len(executed), "executed_in_child": executed}

    def diagnostics():
        from hermes_cli.logs import _read_tail
        from agent.redact import redact_sensitive_text

        return {name: [redact_sensitive_text(line, force=True) for line in
                       _read_tail(home / "logs" / (name + ".log"), 60, has_filters=False)]
                for name in ("gateway", "errors") if (home / "logs" / (name + ".log")).is_file()}

    def stop_runtime():
        from hermes_cli.gateway import stop_profile_gateway, find_gateway_pids

        stop_profile_gateway()
        if find_gateway_pids():
            raise RuntimeError("the rehearsal's gateway survived cleanup")
        return {"stopped": True}

    def legacy_proof():
        memory = io.read_text(home / "memories" / "legacy.md", purpose="memory")
        secret = load_env().get("E2E_LEGACY_SECRET")
        turns = read_raw_config().get("agent", {}).get("max_turns")
        if memory != request["nonce"] or secret != request["nonce"] or turns != request["max_turns"]:
            raise RuntimeError("dirty installation discarded or changed pre-existing state")
        if not (home / "memories" / "legacy.md").read_bytes().startswith(b"HRMVAULT"):
            raise RuntimeError("legacy memory was not encrypted")
        return {"memory": digest(memory), "secret": digest(secret), "config": digest(turns)}

    def resume_proof():
        with SessionDB() as db:
            messages = db.get_messages("e2e-retained")
        verified = len(messages) > 2 and any(row["role"] == "tool" for row in messages[2:])
        if not verified and not request.get("allow_pending"):
            raise RuntimeError("resumed conversation did not persist its new tool activity")
        return {"history_verified": verified}

    handlers = {"seed": seed, "manifest": manifest, "snapshot": snapshot, "restore": restore,
                "legacy-proof": legacy_proof, "resume-proof": resume_proof,
                "update-status": update_status, "live-proof": live_proof, "diagnostics": diagnostics,
                "stop-runtime": stop_runtime}
    print(json.dumps(handlers[request["action"]](), sort_keys=True))


if __name__ == "__main__":
    main()
