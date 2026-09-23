"""Integration: the app's own chokepoints read/write envelopes under a vaulted home."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from hermes_security import errors as he
from hermes_security import vault as hv

PASSWORD = "integration-master-pw"


@pytest.fixture()
def vaulted_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    hv.init_vault(home, PASSWORD)
    hv.unlock(home, PASSWORD)
    yield home
    hv.clear_vault_cache()


def test_config_roundtrip_through_app_loader(vaulted_home):
    from hermes_cli import config as hc

    cfg_path = vaulted_home / "config.yaml"
    # write through the app's one writer
    hc.save_config({"model": "test-model-x", "display": {"interface": "cli"}}, merge_existing=True)
    raw = cfg_path.read_bytes()
    assert b"test-model-x" not in raw, "config.yaml wrote plaintext into a vaulted home"
    assert raw.startswith(b"HRMVAULT")
    # read back through the app's loader
    loaded = hc.read_raw_config()
    assert loaded.get("model") == "test-model-x"


def test_env_roundtrip_through_app_writer(vaulted_home):
    from hermes_cli import config as hc
    from agent import secret_scope

    hc.save_env_value("OPENAI_API_KEY", "sk-integration-123")
    raw = (vaulted_home / ".env").read_bytes()
    assert b"sk-integration-123" not in raw
    assert raw.startswith(b"HRMVAULT")
    parsed = secret_scope.load_env_file(vaulted_home / ".env")
    assert parsed.get("OPENAI_API_KEY") == "sk-integration-123"
    # remove works too
    assert hc.remove_env_value("OPENAI_API_KEY") is True
    parsed2 = secret_scope.load_env_file(vaulted_home / ".env")
    assert "OPENAI_API_KEY" not in parsed2


def test_auth_store_roundtrip(vaulted_home):
    from hermes_cli import auth as ha

    store = ha._empty_auth_store()
    store["providers"]["openai"] = {"api_key": "sk-auth-canary"}
    ha._save_auth_store(store)
    raw = (vaulted_home / "auth.json").read_bytes()
    assert b"sk-auth-canary" not in raw
    assert raw.startswith(b"HRMVAULT")
    reloaded = ha._load_auth_store()
    assert reloaded["providers"]["openai"]["api_key"] == "sk-auth-canary"


def test_state_db_is_sqlcipher_via_sessiondb(vaulted_home):
    sys.modules.setdefault("tests", type(sys)("tests"))
    from hermes_state import SessionDB

    db = SessionDB.__new__(SessionDB)
    db_path = vaulted_home / "state.db"
    from hermes_state_dbfile import _connect_tracked_db

    conn = _connect_tracked_db(str(db_path), check_same_thread=False, timeout=1.0, isolation_level=None)
    try:
        conn.execute("CREATE TABLE messages (body TEXT)")
        conn.execute("INSERT INTO messages VALUES ('session-canary-message')")
        conn.commit()
    finally:
        conn.close()
    raw = db_path.read_bytes()
    assert b"session-canary-message" not in raw
    # stdlib sqlite3 cannot open it
    probe = sqlite3.connect(str(db_path))
    try:
        with pytest.raises(sqlite3.DatabaseError):
            probe.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        probe.close()


def test_whole_home_canary_scan(vaulted_home):
    from agent import secret_scope
    from hermes_cli import auth as ha
    from hermes_cli import config as hc
    from hermes_state_dbfile import _connect_tracked_db

    hc.save_config({"model": "canary-model-xyz"}, merge_existing=True)
    hc.save_env_value("ANTHROPIC_API_KEY", "sk-ant-canary-999")
    store = ha._empty_auth_store()
    store["providers"]["anthropic"] = {"api_key": "auth-canary-key"}
    ha._save_auth_store(store)
    conn = _connect_tracked_db(str(vaulted_home / "state.db"), check_same_thread=False, timeout=1.0, isolation_level=None)
    conn.execute("CREATE TABLE IF NOT EXISTS m (b TEXT)")
    conn.execute("INSERT INTO m VALUES ('state-canary-row')")
    conn.commit()
    conn.close()

    canaries = [b"canary-model-xyz", b"sk-ant-canary-999", b"auth-canary-key", b"state-canary-row"]
    offenders = []
    for path in vaulted_home.rglob("*"):
        if path.is_file() and path.name != ".hermes-vault":
            blob = path.read_bytes()
            for canary in canaries:
                if canary in blob:
                    offenders.append((str(path), canary.decode()))
    assert offenders == [], f"plaintext canaries on disk: {offenders}"
