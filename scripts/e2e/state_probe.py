"""Real state readers/writers used by the encrypted lifecycle VM rehearsal.

Input travels on stdin, never argv/environment. Only semantic hashes leave this
process. No application dependency is mocked or replaced.
"""
from __future__ import annotations

import hashlib
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
    # Do not cache a degraded config parse by importing readers while locked.
    from hermes_cli.config import read_raw_config, save_config, save_env_value, load_env
    from hermes_cli.config_defaults import DEFAULT_CONFIG
    from hermes_state import SessionDB
    from cron.jobs import create_job, pause_job, load_jobs

    def seed():
        credential = request["credential"]
        provider = credential["provider"]
        config = {
            "model": {"provider": provider, "default": credential["model"]},
            "agent": {"max_turns": 17},
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
        save_config(config, merge_existing=True)
        db = SessionDB()
        try:
            db.create_session("e2e-retained", "cli")
            db.append_message("e2e-retained", "user", "retained user message")
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
        if not messages or not all(files.values()):
            raise RuntimeError("retained state is incomplete")
        for path in (home / "config.yaml", home / ".env", home / "memories" / "MEMORY.md"):
            if not path.read_bytes().startswith(b"HRMVAULT"):
                raise RuntimeError(f"state was not sealed: {path.name}")
        if (home / "state.db").read_bytes().startswith(b"SQLite format 3"):
            raise RuntimeError("plaintext session database")
        manifest = {**files, "messages": messages, "jobs": load_jobs(),
                    "config": {key: config[key] for key in ("model", "delegation", "terminal")},
                    "max_turns": config["agent"]["max_turns"],
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
        finally:
            db.close()
        if not calls or not children:
            raise RuntimeError("live task did not actually invoke delegation and persist a child conversation")
        return {"delegation_calls": calls, "child_sessions": children}

    handlers = {"seed": seed, "manifest": manifest, "snapshot": snapshot, "restore": restore,
                "update-status": update_status, "live-proof": live_proof}
    print(json.dumps(handlers[request["action"]](), sort_keys=True))


if __name__ == "__main__":
    main()
