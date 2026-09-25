"""Vault status: inspect without unlocking.

Covers vault_status(), count_encrypted_files(), and the CLI ``vault status``
command's rich output — all WITHOUT importing the crypto stack (metadata-only
scan, no master password).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from hermes_security import vault as hv  # noqa: E402

PASSWORD = "status-test-master-pw"


@pytest.fixture()
def vaulted_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with an initialized+unlocked vault and real data."""
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    hv.init_vault(home, PASSWORD, _allow_existing_state=True)
    hv.unlock(home, PASSWORD)
    yield home
    hv.clear_vault_cache()


@pytest.fixture()
def empty_home(tmp_path, monkeypatch):
    """A home with no vault at all."""
    home = tmp_path / ".hermes2"
    monkeypatch.setenv("HERMES_HOME", str(home))
    yield home
    hv.clear_vault_cache()


# --- vault_status() / count_encrypted_files() -------------------------------


def test_status_no_vault(empty_home):
    """No vault metadata -> status reports exists=False."""
    s = hv.vault_status(empty_home)
    assert s["exists"] is False
    assert s["unlocked"] is False
    assert s["meta"] is None
    assert s["integrity"] == "no_vault"
    assert s["encrypted_files"] == (0, 0, 0)


def test_status_exists_and_unlocked(vaulted_home):
    """Initialized + unlocked home reports correctly."""
    s = hv.vault_status(vaulted_home)
    assert s["exists"] is True
    assert s["unlocked"] is True
    assert s["integrity"] == "ok"
    assert s["meta"] is not None
    assert s["meta"]["kdf"] == "scrypt"
    assert s["meta"]["version"] == 1


def test_status_meta_has_no_secret(vaulted_home):
    """The public metadata file must never contain the password or root key."""
    s = hv.vault_status(vaulted_home)
    dumped = json.dumps(s["meta"]).lower()
    assert PASSWORD.lower() not in dumped
    for forbidden in ("master", "root_key", "password", "key"):
        assert forbidden not in {k.lower() for k in s["meta"]}
    # salt is present (public) but not the verifier's plaintext
    assert "salt" in s["meta"]
    assert "verifier" in s["meta"]


def test_status_locked_refuses(vaulted_home):
    """Vault exists but is locked -> unlocked=False."""
    hv.lock_now(vaulted_home)
    s = hv.vault_status(vaulted_home)
    assert s["exists"] is True
    assert s["unlocked"] is False
    # status still works without unlocking (re-unlock for other tests)
    hv.unlock(vaulted_home, PASSWORD)


def test_status_after_relock_reunlock(vaulted_home):
    """Lock -> status shows locked -> unlock -> status shows unlocked."""
    s1 = hv.vault_status(vaulted_home)
    assert s1["unlocked"] is True
    hv.lock_now(vaulted_home)
    s2 = hv.vault_status(vaulted_home)
    assert s2["unlocked"] is False
    hv.unlock(vaulted_home, PASSWORD)
    s3 = hv.vault_status(vaulted_home)
    assert s3["unlocked"] is True


# --- count_encrypted_files() -----------------------------------------------


def test_count_no_encrypted_files(vaulted_home):
    """Fresh vault with no data files shows zero encrypted files."""
    env, dbs, frames = hv.count_encrypted_files(vaulted_home)
    assert env == 0
    assert dbs == 0
    assert frames == 0


def test_count_envelopes_and_dbs(vaulted_home):
    """Write envelopes + a SQLCipher DB; count reflects them."""
    from hermes_security import sqlite as hsql

    v = hv.get_vault(vaulted_home)
    # envelopes
    v.write_bytes("config.yaml", b"model: test\n", purpose="config")
    v.write_bytes(".env", b"KEY=val\n", purpose="env")
    # SQLCipher DB
    conn = hsql.connect(vaulted_home / "state.db")
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.execute("INSERT INTO t VALUES ('secret-data')")
    conn.commit()
    conn.close()
    # frame streams
    from hermes_security import frames

    frames.append(vaulted_home / "logs" / "agent.log", b"log-line\n", purpose="log")
    frames.append(vaulted_home / "sessions" / "2026.jsonl", b"frame-data\n", purpose="transcript")

    env, dbs, frames_count = hv.count_encrypted_files(vaulted_home)
    assert env == 2  # config.yaml + .env (envelopes)
    assert dbs == 1  # state.db (SQLCipher)
    assert frames_count == 2  # agent.log + 2026.jsonl (frame streams)


def test_count_skips_code_trees(vaulted_home):
    """Files inside hermes-agent/.venv/node_modules are NOT counted."""
    from hermes_security import sqlite as hsql

    v = hv.get_vault(vaulted_home)
    # user state
    v.write_bytes("config.yaml", b"a: 1\n", purpose="config")
    # code tree (must be skipped)
    (vaulted_home / "hermes-agent" / "run_agent.py").parent.mkdir(parents=True, exist_ok=True)
    (vaulted_home / "hermes-agent" / "run_agent.py").write_text("print('hi')\n")
    (vaulted_home / ".venv" / "bin" / "python").parent.mkdir(parents=True, exist_ok=True)
    (vaulted_home / ".venv" / "bin" / "python").write_text("#!/bin/sh\n")
    (vaulted_home / "node_modules" / "pkg" / "index.js").parent.mkdir(parents=True, exist_ok=True)
    (vaulted_home / "node_modules" / "pkg" / "index.js").write_text("// code\n")

    env, dbs, frames_count = hv.count_encrypted_files(vaulted_home)
    assert env == 1  # only config.yaml
    assert dbs == 0
    assert frames_count == 0


def test_count_skips_lock_sidecars(vaulted_home):
    """``.lock`` advisory sidecars are not counted as encrypted files."""
    from hermes_security import sqlite as hsql

    v = hv.get_vault(vaulted_home)
    v.write_bytes("config.yaml", b"a: 1\n", purpose="config")
    # write a .lock file (plaintext, like flock sidecars)
    (vaulted_home / "config.yaml.lock").write_bytes(b"flock-sidecar\n")

    env, dbs, frames_count = hv.count_encrypted_files(vaulted_home)
    assert env == 1
    assert dbs == 0
    assert frames_count == 0


def test_count_skips_vault_meta(vaulted_home):
    """The ``.hermes-vault`` metadata file itself is not counted."""
    from hermes_security import sqlite as hsql

    v = hv.get_vault(vaulted_home)
    v.write_bytes("config.yaml", b"a: 1\n", purpose="config")

    env, dbs, frames_count = hv.count_encrypted_files(vaulted_home)
    assert env == 1  # only config.yaml, NOT .hermes-vault


def test_count_nonexistent_home(tmp_path):
    """Scanning a non-existent directory returns zeros without error."""
    env, dbs, frames_count = hv.count_encrypted_files(tmp_path / "does-not-exist")
    assert (env, dbs, frames_count) == (0, 0, 0)


# --- CLI command ------------------------------------------------------------


def test_cli_status_no_vault(empty_home, capsys):
    """``hermes vault status`` on a vault-less home returns rc=1 with instructions."""
    from types import SimpleNamespace

    from hermes_cli.vault_cmd import cmd_vault

    rc = cmd_vault(SimpleNamespace(vault_command="status"))
    assert rc == 1
    out = capsys.readouterr().out
    assert "No vault" in out
    assert "refused" in out or "migrate" in out or "init" in out


def test_cli_status_rich_output(vaulted_home, capsys):
    """``hermes vault status`` on a vaulted home prints KDF params + file counts."""
    from types import SimpleNamespace

    from hermes_cli.vault_cmd import cmd_vault

    # add some data first
    from hermes_security import sqlite as hsql

    v = hv.get_vault(vaulted_home)
    v.write_bytes("config.yaml", b"model: rich-status-test\n", purpose="config")
    conn = hsql.connect(vaulted_home / "state.db")
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.commit()
    conn.close()

    rc = cmd_vault(SimpleNamespace(vault_command="status"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Vault present" in out
    assert "Integrity" in out
    assert "scrypt" in out
    assert "Encrypted files" in out
    assert "envelope" in out
    assert "database" in out
    assert "unlock" in out.lower() or "HERMES_VAULT_PRIVATE_KEY" in out


def test_cli_status_locked(vaulted_home, capsys):
    """Status shows unlocked=no when the vault is locked in this process."""
    from types import SimpleNamespace

    from hermes_cli.vault_cmd import cmd_vault

    from hermes_security import sqlite as hsql

    v = hv.get_vault(vaulted_home)
    v.write_bytes("config.yaml", b"a: 1\n", purpose="config")
    hv.lock_now(vaulted_home)

    rc = cmd_vault(SimpleNamespace(vault_command="status"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Unlocked in proc : no" in out

    # re-unlock for fixture teardown
    hv.unlock(vaulted_home, PASSWORD)
