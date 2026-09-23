"""Security core tracer bullet: init -> unlock -> encrypt -> SQLCipher -> frames -> attacks."""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from hermes_security import errors as he
from hermes_security import frames, sqlite as hsqlite
from hermes_security import vault as hv

PASSWORD = "correct horse battery staple"


@pytest.fixture()
def fresh_home(tmp_path, monkeypatch):
    """An isolated HERMES_HOME with an unlocked vault."""

    home = tmp_path / "home" / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    hv.init_vault(home, PASSWORD)
    hv.unlock(home, PASSWORD)
    yield home
    hv.clear_vault_cache()


def test_vault_init_refuses_preexisting_state(tmp_path):
    dirty = tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "state.db").write_bytes(b"legacy plaintext")
    with pytest.raises(he.PlaintextStateError):
        hv.init_vault(dirty, "pw")


def test_vault_double_init_refused(fresh_home):
    with pytest.raises(he.VaultError):
        hv.init_vault(fresh_home, "other")


def test_wrong_password_rejected(fresh_home):
    hv.clear_vault_cache()
    with pytest.raises(he.WrongMasterPasswordError):
        hv.unlock(fresh_home, "wrong password")
    v = hv.unlock(fresh_home, PASSWORD)
    assert v.write_bytes("config.yaml", b"k: v", purpose="config") == fresh_home / "config.yaml"


def test_envelope_roundtrip_and_restart(fresh_home):
    v = hv.get_vault(fresh_home)
    secret = "api_key=sk-live-12345\n".encode()
    v.write_bytes(".env", secret, purpose="env")
    on_disk = (fresh_home / ".env").read_bytes()
    assert b"sk-live" not in on_disk
    assert on_disk.startswith(b"HRMVAULT")
    hv.clear_vault_cache()
    v2 = hv.unlock(fresh_home, PASSWORD)
    assert v2.read_bytes(".env", purpose="env") == secret


def test_tampered_envelope_rejected(fresh_home):
    v = hv.get_vault(fresh_home)
    v.write_bytes("config.yaml", b"a: 1\n", purpose="config")
    blob = (fresh_home / "config.yaml").read_bytes()
    tampered = blob[:-1] + bytes([blob[-1] ^ 0xFF])
    (fresh_home / "config.yaml").write_bytes(tampered)
    with pytest.raises(he.VaultIntegrityError):
        v.read_bytes("config.yaml", purpose="config")


def test_purpose_swap_rejected(fresh_home):
    v = hv.get_vault(fresh_home)
    v.write_bytes(".env", b"SECRET=1\n", purpose="env")
    with pytest.raises(he.VaultIntegrityError):
        v.read_bytes(".env", purpose="config")


def test_locked_refusal(fresh_home):
    v = hv.get_vault(fresh_home)
    v.write_bytes("config.yaml", b"x: y\n", purpose="config")
    hv.lock_now(fresh_home)
    with pytest.raises(he.VaultLockedError):
        hv.get_vault(fresh_home)


def test_sqlcipher_round_trip_hides_plaintext(fresh_home):
    conn = hsqlite.connect(fresh_home / "state.db")
    conn.execute("CREATE TABLE t (secret TEXT)")
    conn.execute("INSERT INTO t VALUES ('canary-gold-secret')")
    conn.commit()
    conn.close()
    raw = (fresh_home / "state.db").read_bytes()
    assert b"canary-gold-secret" not in raw
    probe = sqlite3.connect(str(fresh_home / "state.db"))
    try:
        with pytest.raises(sqlite3.DatabaseError):
            probe.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        probe.close()


def test_sqlcipher_reopen_after_restart(fresh_home):
    conn = hsqlite.connect(fresh_home / "state.db")
    conn.execute("CREATE TABLE IF NOT EXISTS t (secret TEXT)")
    conn.execute("INSERT INTO t VALUES ('persisted-secret')")
    conn.commit()
    conn.close()
    hv.clear_vault_cache()
    hv.unlock(fresh_home, PASSWORD)
    conn2 = hsqlite.connect(fresh_home / "state.db")
    rows = conn2.execute("SELECT secret FROM t").fetchall()
    conn2.close()
    assert rows == [("persisted-secret",)]


def test_sqlcipher_key_bound_to_path(fresh_home):
    conn = hsqlite.connect(fresh_home / "state.db")
    conn.execute("CREATE TABLE t (x INT)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()
    conn.close()
    other_dir = fresh_home / "sessions"
    other_dir.mkdir(exist_ok=True)
    shutil.copy(fresh_home / "state.db", other_dir / "state.db")
    # Same filename, different path -> different derived key -> refused at
    # connect time (the schema-read verification), not silently re-keyed.
    with pytest.raises(hsqlite.DatabaseError):
        hsqlite.connect(other_dir / "state.db")


def test_frames_roundtrip_truncation_and_tamper(fresh_home):
    log = fresh_home / "logs" / "agent.log"
    frames.append(log, b"line one with secret-alpha\n", purpose="log")
    frames.append(log, b"line two with secret-beta\n", purpose="log")
    raw = log.read_bytes()
    assert b"secret-alpha" not in raw
    assert b"secret-beta" not in raw
    got = list(frames.read_frames(log, purpose="log"))
    assert got == [b"line one with secret-alpha\n", b"line two with secret-beta\n"]
    truncated = raw[: len(raw) - 10]
    log.write_bytes(truncated)
    got2 = list(frames.read_frames(log, purpose="log"))
    assert got2 == [b"line one with secret-alpha\n"]
    with pytest.raises(he.VaultIntegrityError):
        list(frames.read_frames(log, purpose="other"))


def test_recursive_canary_scan(fresh_home):
    v = hv.get_vault(fresh_home)
    v.write_bytes("config.yaml", b"model: gpt-test\nprovider: openai\n", purpose="config")
    v.write_bytes(".env", b"OPENAI_API_KEY=sk-canary-abc\n", purpose="env")
    v.write_bytes("auth.json", json.dumps({"tokens": "tok-canary"}).encode(), purpose="auth")
    conn = hsqlite.connect(fresh_home / "state.db")
    conn.execute("CREATE TABLE msgs (body TEXT)")
    conn.execute("INSERT INTO msgs VALUES ('db-canary-message')")
    conn.commit()
    conn.close()
    frames.append(fresh_home / "logs" / "agent.log", b"log-canary-entry\n", purpose="log")
    (fresh_home / "sessions").mkdir(exist_ok=True)
    frames.append(fresh_home / "sessions" / "2026.jsonl", json.dumps({"user": "jsonl-canary"}).encode(), purpose="transcript")

    canaries = [
        b"sk-canary-abc",
        b"tok-canary",
        b"db-canary-message",
        b"log-canary-entry",
        b"jsonl-canary",
        b"gpt-test",
    ]
    offenders = []
    for path in fresh_home.rglob("*"):
        if path.is_file() and path.name != ".hermes-vault":
            blob = path.read_bytes()
            for canary in canaries:
                if canary in blob:
                    offenders.append((str(path), canary.decode()))
    assert offenders == [], f"plaintext canaries found on disk: {offenders}"


def test_meta_file_is_public_safe(fresh_home):
    meta = json.loads((fresh_home / ".hermes-vault").read_text())
    assert meta["kdf"] == "scrypt"
    dumped = json.dumps(meta).lower()
    assert "correct horse" not in dumped
    assert "staple" not in dumped
    for forbidden in ("key", "master", "root_key", "password"):
        assert forbidden not in {k.lower() for k in meta}
