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
    from hermes_cli.config import read_raw_config, save_config, save_env_value, load_env
    from hermes_state import SessionDB
    from cron.jobs import save_jobs, load_jobs

    home = get_hermes_home()
    vault.unlock(home, request["password"])
    action = request["action"]
    if action == "seed":
        credential = request["credential"]
        provider = credential["provider"]
        config = {
            "model": {"provider": provider, "default": credential["model"]},
            "agent": {"max_turns": 17},
            "delegation": {"max_concurrent_children": 2, "max_iterations": 8},
            "terminal": {"backend": "local", "cwd": str(request["workspace"])},
            "curator": {"enabled": False},
            "updates": {"auto_update": False},
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
        save_jobs([{"id": "e2e-retained", "name": "retained job", "enabled": False,
                    "prompt": "Retain this disabled fixture; never run it.",
                    "schedule": {"kind": "interval", "minutes": 9999}}])
    elif action == "snapshot":
        from hermes_cli.backup import create_quick_snapshot
        snapshot = create_quick_snapshot(label="e2e-restore", hermes_home=home)
        if not snapshot:
            raise RuntimeError("real snapshot creation failed")
        print(json.dumps({"snapshot": snapshot}))
        return
    elif action == "restore":
        from hermes_cli.backup import restore_quick_snapshot
        save_env_value("E2E_RETAINED_SECRET", "deliberately-mutated")
        save_config({"agent": {"max_turns": 3}}, merge_existing=True)
        if not restore_quick_snapshot(request["snapshot"], hermes_home=home):
            raise RuntimeError("real snapshot restoration failed")
    elif action != "manifest":
        raise ValueError(action)

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
    print(json.dumps({key: digest(value) for key, value in manifest.items()}, sort_keys=True))


if __name__ == "__main__":
    main()
