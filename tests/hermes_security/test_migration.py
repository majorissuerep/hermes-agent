"""Migration e2e: realistic plaintext home -> vaulted, zero loss, zero plaintext left."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from hermes_security import migrate as mig
from hermes_security import vault as hv

PW = "migration-test-password"


def _build_plain_home(root: Path) -> Path:
    home = root / ".hermes"
    # config + secrets
    (home).mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text("model:\n  default: canary-model-mig\n  provider: custom:ml-gateway\n")
    (home / ".env").write_text("OPENAI_API_KEY=sk-mig-canary-123\nML_GATEWAY_API_KEY=gw-mig-canary\n")
    (home / "auth.json").write_text(json.dumps({"providers": {"nous": {"token": "tok-mig-canary"}}}))
    (home / "gateway_state.json").write_text(json.dumps({"pid": 12345, "updated_at": "2026-09-23T00:00:00Z"}))
    (home / ".update_check").write_text(json.dumps({"ts": 1.0, "behind": 0, "ver": "0.21.4"}))
    (home / "SOUL.md").write_text("You are Hermes. Persona text canary.\n")
    # memories
    (home / "memories").mkdir()
    (home / "memories" / "MEMORY.md").write_text("entry-one-mig-canary\n§\nentry-two\n")
    (home / "memories" / "USER.md").write_text("User likes canary-mig-coffee\n")
    # logs
    (home / "logs").mkdir()
    (home / "logs" / "agent.log").write_text("2026-09-23 info line-mig-canary-alpha\n2026-09-23 warn line-two\n")
    (home / "logs" / "errors.log").write_text("2026-09-23 error err-mig-canary\n")
    # sessions jsonl + request dumps
    (home / "sessions").mkdir()
    (home / "sessions" / "20260923_100000_aa.jsonl").write_text(
        json.dumps({"role": "user", "content": "jsonl-mig-canary"}) + "\n"
    )
    (home / "sessions" / "request_dump_1.json").write_text(json.dumps({"dump": "req-mig-canary"}))
    # state.db with real schema + FTS5
    conn = sqlite3.connect(home / "state.db")
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, model TEXT)")
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT)")
    conn.execute("CREATE VIRTUAL TABLE messages_fts USING fts5(content)")
    conn.execute("INSERT INTO sessions VALUES ('mig-sess-1', 'canary-model-mig')")
    conn.execute("INSERT INTO messages (session_id, role, content) VALUES ('mig-sess-1', 'user', 'db-mig-canary-message')")
    conn.execute("INSERT INTO messages_fts VALUES ('db-mig-canary-message')")
    conn.commit()
    conn.close()
    # a second DB (kanban)
    conn = sqlite3.connect(home / "kanban.db")
    conn.execute("CREATE TABLE boards (name TEXT)")
    conn.execute("INSERT INTO boards VALUES ('board-mig-canary')")
    conn.commit()
    conn.close()
    # code tree stays public
    (home / "hermes-agent" / ".git").mkdir(parents=True, exist_ok=True)
    (home / "hermes-agent" / "run_agent.py").write_text("# public code\n")
    return home


CANARIES = [
    b"canary-model-mig",
    b"sk-mig-canary-123",
    b"gw-mig-canary",
    b"tok-mig-canary",
    b"entry-one-mig-canary",
    b"canary-mig-coffee",
    b"line-mig-canary-alpha",
    b"err-mig-canary",
    b"jsonl-mig-canary",
    b"req-mig-canary",
    b"db-mig-canary-message",
    b"board-mig-canary",
    b"Persona text canary",
]


def test_full_migration(tmp_path, monkeypatch):
    home = _build_plain_home(tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))

    report = mig.migrate_home(home, PW)
    assert report.ok, f"migration failures: {report.failures}"
    assert report.backup_path and report.backup_path.exists()
    assert len(report.databases) == 2
    assert "config.yaml" in report.envelopes
    assert ".env" in report.envelopes

    # vault now exists and unlocks
    assert hv.vault_exists(home)
    hv.clear_vault_cache()
    v = hv.unlock(home, PW)

    # 1. zero plaintext anywhere in the home (except code trees)
    offenders = []
    for p in home.rglob("*"):
        if p.is_file() and "hermes-agent" not in p.parts:
            blob = p.read_bytes()
            for canary in CANARIES:
                if canary in blob:
                    offenders.append((str(p.relative_to(home)), canary.decode()))
    assert offenders == [], f"plaintext canaries on disk: {offenders}"

    # 2. state.db still opens through the app layer with data intact
    from hermes_security import sqlite as hsql

    conn = hsql.connect(home / "state.db")
    rows = conn.execute("SELECT content FROM messages").fetchall()
    fts = conn.execute("SELECT * FROM messages_fts WHERE messages_fts MATCH 'canary'").fetchall()
    sess = conn.execute("SELECT model FROM sessions").fetchone()
    conn.close()
    assert rows == [("db-mig-canary-message",)]
    assert fts, "FTS5 lost in migration"
    assert sess == ("canary-model-mig",)

    # 3. kanban.db intact
    conn = hsql.connect(home / "kanban.db")
    assert conn.execute("SELECT name FROM boards").fetchone() == ("board-mig-canary",)
    conn.close()

    # 4. config loads through the app loader
    from hermes_cli import config as hc

    cfg = hc.load_config()
    assert cfg.get("model", {}).get("default") == "canary-model-mig"

    # 5. .env parses through the app tokenizer
    from agent.secret_scope import load_env_file

    env = load_env_file(home / ".env")
    assert env.get("OPENAI_API_KEY") == "sk-mig-canary-123"
    assert env.get("ML_GATEWAY_API_KEY") == "gw-mig-canary"

    # 6. auth store loads
    from hermes_cli import auth as ha

    store = ha._load_auth_store(str(home / "auth.json"))
    assert store["providers"]["nous"]["token"] == "tok-mig-canary"

    # 7. memories load through the memory store reader
    from tools.memory_tool_store import MemoryStore

    raw = MemoryStore._read_raw_checked(home / "memories" / "MEMORY.md")
    assert raw[1] and "entry-one-mig-canary" in raw[0]

    # 8. stdlib sqlite3 cannot open the migrated DBs
    probe = sqlite3.connect(str(home / "state.db"))
    try:
        with pytest.raises(sqlite3.DatabaseError):
            probe.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        probe.close()

    # 9. scan_for_plaintext reports clean
    assert mig.scan_for_plaintext(home) == []

    # 10. restart simulation: unlock in a fresh process state, everything still works
    hv.clear_vault_cache()
    hv.unlock(home, PW)
    conn = hsql.connect(home / "state.db")
    assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 1
    conn.close()


def test_dry_run_touches_nothing(tmp_path):
    home = _build_plain_home(tmp_path)
    before = (home / "config.yaml").read_bytes()
    report = mig.migrate_home(home, PW, dry_run=True)
    assert (home / "config.yaml").read_bytes() == before
    assert not hv.vault_exists(home)
    assert report.databases  # classified, not converted


def test_wrong_password_after_migration(tmp_path):
    home = _build_plain_home(tmp_path)
    mig.migrate_home(home, PW)
    hv.clear_vault_cache()
    with pytest.raises(Exception):
        hv.unlock(home, "wrong-password")
