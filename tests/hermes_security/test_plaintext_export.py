"""Real encrypted-home recovery: decrypt a copy, verify it, and migrate it again."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import tarfile

import pytest

from hermes_security import frames, io, migrate, sqlite as encrypted_sqlite, vault

pytestmark = pytest.mark.platforms("posix")


def _home(tmp_path, monkeypatch):
    home = tmp_path / "encrypted"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / "config.yaml").write_text("# keep comments\nagent:\n  max_turns: 37\n")
    (home / ".env").write_text("RECOVERY_API_KEY=synthetic-recovery-secret\n")
    (home / "logs").mkdir()
    (home / "logs" / "agent.log").write_text("first line\n")
    (home / "skills" / "retained").mkdir(parents=True)
    (home / "skills" / "retained" / "SKILL.md").write_text("# User-created skill\n")
    (home / "vault").mkdir()
    (home / "vault" / "vault.key").write_bytes(b"synthetic-browser-vault-key")
    (home / "vault.key").write_bytes(b"synthetic-master-unlock-material")
    with sqlite3.connect(home / "state.db") as db:
        db.execute("CREATE TABLE retained (id INTEGER PRIMARY KEY, value BLOB)")
        db.execute("INSERT INTO retained VALUES (1, ?)", (b"retained-row\x00\xff",))
        db.execute("PRAGMA user_version=19")
        db.execute("PRAGMA application_id=8127")
    assert migrate.migrate_home(home, "synthetic-export-password").ok
    io.write_bytes(home / "attachments" / "retained.bin", b"attachment\x00\xff", purpose="state")
    frames.append(home / "logs" / "agent.log", b"second line", purpose="log")
    return home


def _contents(home):
    with encrypted_sqlite.connect(home / "state.db") as db:
        rows = db.execute("SELECT * FROM retained ORDER BY id").fetchall()
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    return (
        io.read_bytes(home / "config.yaml", purpose="config"),
        io.read_bytes(home / ".env", purpose="env"),
        io.read_bytes(home / "attachments" / "retained.bin", purpose="state"),
        list(frames.read_frames(home / "logs" / "agent.log", purpose="log")),
        rows,
        io.read_bytes(home / "vault" / "vault.key", purpose="state"),
    )


def test_export_round_trips_sqlcipher_wal_envelopes_streams_and_user_skills(tmp_path, monkeypatch):
    from hermes_security.export import export_home

    home = _home(tmp_path, monkeypatch)
    connection = encrypted_sqlite.connect(home / "state.db")
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("INSERT INTO retained VALUES (2, ?)", (b"committed-in-wal",))
        connection.commit()
        before = _contents(home)
        raw_before = (home / "config.yaml").read_bytes()
        archive = tmp_path / "recovery.tar"
        report = export_home(home, archive)
        assert report["files"] >= 6 and report["databases"] == 1
        assert archive.stat().st_mode & 0o777 == 0o600
        recovered = tmp_path / "recovered"
        recovered.mkdir()
        with tarfile.open(archive) as tar:
            assert all(member.isfile() and member.mode & 0o077 == 0 for member in tar)
            tar.extractall(recovered, filter="data")
        assert not (recovered / ".hermes-vault").exists()
        manifest = json.loads((recovered / ".hermes-export.json").read_text())
        assert manifest["format"] == 1 and "state.db" in manifest["sha256"]
        with sqlite3.connect(recovered / "state.db") as plain:
            assert plain.execute("SELECT * FROM retained ORDER BY id").fetchall() == before[-2]
            assert plain.execute("PRAGMA user_version").fetchone()[0] == 19
            assert plain.execute("PRAGMA application_id").fetchone()[0] == 8127
        assert (recovered / "logs" / "agent.log").read_bytes() == b"first line\nsecond line\n"
        assert (recovered / "skills" / "retained" / "SKILL.md").read_text() == "# User-created skill\n"
        assert not (recovered / "vault.key").exists()
        assert (recovered / "vault" / "vault.key").read_bytes() == b"synthetic-browser-vault-key"
        assert _contents(home) == before and (home / "config.yaml").read_bytes() == raw_before
        monkeypatch.setenv("HERMES_HOME", str(recovered))
        assert migrate.migrate_home(recovered, "new-recovery-password").ok
        assert _contents(recovered) == before
    finally:
        connection.close()


@pytest.mark.parametrize("fault", ["corrupt-envelope", "truncated-stream", "existing-target", "inside-home"])
def test_export_refuses_incomplete_or_unsafe_output_without_mutating_source(tmp_path, monkeypatch, fault):
    from hermes_security.export import export_home

    home = _home(tmp_path, monkeypatch)
    archive = tmp_path / "recovery.tar"
    if fault == "corrupt-envelope":
        path = home / ".env"
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
    elif fault == "truncated-stream":
        path = home / "logs" / "agent.log"
        path.write_bytes(path.read_bytes()[:-1])
    elif fault == "existing-target":
        archive.write_bytes(b"retain-existing-export")
    else:
        archive = home / "recovery.tar"
    original = {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    with pytest.raises((ValueError, OSError, vault.VaultError)):
        export_home(home, archive)
    assert archive.read_bytes() == b"retain-existing-export" if fault == "existing-target" else not archive.exists()
    assert {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()} == original
    assert not list(tmp_path.glob(".hermes-export-*"))


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permissions")
def test_export_fails_closed_on_an_unreadable_state_directory(tmp_path, monkeypatch):
    from hermes_security.export import export_home

    home = _home(tmp_path, monkeypatch)
    hidden = home / "unreadable"
    hidden.mkdir()
    io.write_text(hidden / "retained.txt", "must not silently disappear", purpose="state")
    hidden.chmod(0)
    try:
        with pytest.raises(PermissionError):
            export_home(home, tmp_path / "recovery.tar")
    finally:
        hidden.chmod(0o700)
    assert not (tmp_path / "recovery.tar").exists()
