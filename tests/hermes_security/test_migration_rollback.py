"""Migration never creates a plaintext rollback, and explicit continuation preserves exact original meaning."""
from __future__ import annotations

from pathlib import Path
import secrets
import sqlite3
import tarfile

import pytest

from hermes_security import migrate, migration_state, vault
from hermes_security.artifacts import _Custody
from hermes_security.errors import VaultError
from hermes_security.migration_archive import read_backup_metadata, verified_tar

PASSWORD = "synthetic-rollback-credential"


@pytest.fixture
def plain(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    nonce = secrets.token_hex(24).encode()
    for name, prefix in (("config.yaml", b"model: "), (".env", b"API_TOKEN="), ("SOUL.md", b"persona ")):
        (home / name).write_bytes(prefix + nonce + b"\n")
    (home / "logs").mkdir()
    (home / "logs" / "agent.log").write_bytes(b"first\r\n" + nonce + b"\npartial")
    (home / "sessions").mkdir()
    (home / "sessions" / "one.jsonl").write_bytes(b'{"value":"' + nonce + b'"}\n\n')
    with sqlite3.connect(home / "state.db") as connection:
        connection.executescript("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT); PRAGMA user_version=42;")
        connection.execute("INSERT INTO sample VALUES (1, ?)", (nonce.decode(),))
    connection.close()
    yield home, nonce
    vault.clear_vault_cache()


def _snapshot(home):
    return {p.relative_to(home).as_posix(): p.read_bytes() for p in home.rglob("*") if p.is_file()}


def _originals(path, home):
    with verified_tar(path, custody=_Custody(home)) as archive:
        return {member.name: archive.extractfile(member).read() for member in archive if member.isfile()}


@pytest.mark.parametrize("key_only", [False, True], ids=["passphrase", "external-key-slot"])
def test_migration_rollback_is_encrypted_standalone_and_repairs_original_env(plain, tmp_path, key_only):
    home, nonce = plain
    original = _snapshot(home)
    private, public = vault.generate_keypair()
    report = migrate.migrate_home(home, PASSWORD, public_key_raw=public if key_only else None)
    assert report.ok, report.failures
    assert report.backup_path.suffix == ".hmb"
    backup = report.backup_path.read_bytes()
    assert nonce not in backup and PASSWORD.encode() not in backup
    assert not migration_state.pending(home)
    assert _originals(report.backup_path, home) == original
    assert not any(nonce in p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())
    recovered = tmp_path / "isolated"
    recovered.mkdir(mode=0o700)
    vault._atomic_write(recovered / ".hermes-vault", read_backup_metadata(report.backup_path))
    vault.clear_vault_cache()
    if key_only:
        vault.unlock_with_private_key(recovered, private)
    else:
        vault.unlock(recovered, PASSWORD)
    assert _originals(report.backup_path, recovered) == original
    vault.clear_vault_cache()
    vault.unlock(home, PASSWORD)
    damaged = bytearray((home / ".env").read_bytes())
    damaged[-1] ^= 1
    (home / ".env").write_bytes(damaged)
    repaired = migrate.repair_clobbered_state(home)
    assert repaired["restored"] and not repaired["unrecoverable"], repaired
    assert vault.get_vault(home).read_bytes(home / ".env", purpose="env") == original[".env"]
    assert report.backup_path.read_bytes() == backup


@pytest.mark.parametrize("kind", ["db", "frames", "envelope"])
def test_interruption_after_atomic_conversion_resumes_without_double_encryption(plain, monkeypatch, kind):
    home, nonce = plain
    attribute = {"db": "_migrate_database", "frames": "_migrate_frames", "envelope": "_migrate_envelope"}[kind]
    original = getattr(migrate, attribute)
    interrupted = []

    def converted_then_crashed(*args):
        original(*args)
        if not interrupted:
            interrupted.append(args[2])
            raise KeyboardInterrupt("synthetic process interruption after the atomic replacement")

    monkeypatch.setattr(migrate, attribute, converted_then_crashed)
    with pytest.raises(KeyboardInterrupt):
        migrate.migrate_home(home, PASSWORD)
    unlocked = vault.get_vault(home)
    state = migration_state.read(home, unlocked)
    assert state["phase"] == "converting" and state["current"]["kind"] == kind
    backup = Path(state["backup"])
    identity = backup.read_bytes()
    vault.clear_vault_cache()
    monkeypatch.setattr(migrate, attribute, original)
    resumed = migrate.migrate_home(home, PASSWORD)
    assert resumed.ok, resumed.failures
    assert not migration_state.pending(home) and backup.read_bytes() == identity
    assert interrupted[0].as_posix() + " (verified converted content)" in resumed.skipped
    from hermes_security.sqlite import connect

    connection = connect(home / "state.db")
    try:
        assert connection.execute("SELECT value FROM sample").fetchall() == [(nonce.decode(),)]
        assert connection.execute("PRAGMA user_version").fetchone() == (42,)
    finally:
        connection.close()
    assert not migrate.scan_for_plaintext(home)


@pytest.mark.parametrize("damage", ["truncate", "tamper"])
def test_damaged_pending_rollback_refuses_before_any_source_file_changes(plain, monkeypatch, damage):
    from hermes_security import migration_workflow

    home, _nonce = plain
    original = migration_workflow._convert_member

    def crash_before_conversion(*args):
        raise KeyboardInterrupt("synthetic stop before the first conversion")

    monkeypatch.setattr(migration_workflow, "_convert_member", crash_before_conversion)
    with pytest.raises(KeyboardInterrupt):
        migrate.migrate_home(home, PASSWORD)
    state = migration_state.read(home, vault.get_vault(home))
    path = Path(state["backup"])
    raw = path.read_bytes()
    path.write_bytes(raw[:-7] if damage == "truncate" else raw[:-1] + bytes([raw[-1] ^ 1]))
    before = _snapshot(home)
    monkeypatch.setattr(migration_workflow, "_convert_member", original)
    with pytest.raises(VaultError):
        migrate.migrate_home(home, PASSWORD)
    assert _snapshot(home) == before
    assert migration_state.pending(home)


def test_legacy_raw_tar_is_passive_repair_input_and_never_rewritten(plain):
    home, _nonce = plain
    original = (home / ".env").read_bytes()
    legacy = home.parent / f"hermes-premigration-{home.name}-zzzz-legacy.tar"
    with tarfile.open(legacy, "w") as archive:
        archive.add(home / ".env", arcname=".env")
    previous = legacy.read_bytes()
    report = migrate.migrate_home(home, PASSWORD)
    assert report.ok, report.failures
    damaged = bytearray((home / ".env").read_bytes())
    damaged[-1] ^= 1
    (home / ".env").write_bytes(damaged)
    repaired = migrate.repair_clobbered_state(home)
    assert repaired["restored"] and not repaired["unrecoverable"], repaired
    assert vault.get_vault(home).read_bytes(home / ".env", purpose="env") == original
    assert legacy.read_bytes() == previous


def test_unknown_initialized_vault_without_checkpoint_cannot_resume(plain):
    home, _nonce = plain
    vault.init_vault(home, PASSWORD, _allow_existing_state=True)
    before = _snapshot(home)
    with pytest.raises(VaultError, match="without an authenticated pending"):
        migrate.migrate_home(home, PASSWORD)
    # Exclusive maintenance may create only its existing empty lease; user bytes stay exact.
    assert all((home / name).read_bytes() == data for name, data in before.items())


@pytest.mark.parametrize("drift", [False, True], ids=["checkpoint-only", "changed-row"])
def test_interrupted_sqlite_checkpoint_resumes_only_original_logical_state(plain, monkeypatch, drift):
    home, nonce = plain
    original = migrate._checkpoint_plain_db
    before = (home / "state.db").read_bytes()

    def checkpoint_then_crashed(path):
        original(path)
        # A real SQLite layout change models a checkpoint/VACUUM before replacement;
        # physical bytes change while the original schema/data retain their meaning.
        connection = sqlite3.connect(path)
        connection.execute("VACUUM")
        connection.close()
        raise KeyboardInterrupt("synthetic stop after the plaintext database checkpoint")

    monkeypatch.setattr(migrate, "_checkpoint_plain_db", checkpoint_then_crashed)
    with pytest.raises(KeyboardInterrupt):
        migrate.migrate_home(home, PASSWORD)
    assert (home / "state.db").read_bytes() != before
    state = migration_state.read(home, vault.get_vault(home))
    assert state["current"]["path"] == "state.db"
    if drift:
        connection = sqlite3.connect(home / "state.db")
        connection.execute("UPDATE sample SET value='unexpected source change'")
        connection.commit(); connection.close()
    snapshot = _snapshot(home)
    monkeypatch.setattr(migrate, "_checkpoint_plain_db", original)
    report = migrate.migrate_home(home, PASSWORD)
    if drift:
        assert not report.ok and "changed" in report.failures[0]
        assert all((home / name).read_bytes() == data for name, data in snapshot.items())
    else:
        assert report.ok, report.failures
        from hermes_security.sqlite import connect

        connection = connect(home / "state.db")
        try:
            assert connection.execute("SELECT value FROM sample").fetchall() == [(nonce.decode(),)]
        finally:
            connection.close()
