"""Selected-home migration stops at its direct profiles subtree; child islands stay exact and opaque.

The full encrypted rollback still carries every child's bytes, including each child's own public
credential metadata, and a named home's rollback is stored outside every vaulted ancestor.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat

import pytest

pytestmark = pytest.mark.platforms("linux", "macos")

from hermes_security import migrate, migration_profiles, vault
from hermes_security.artifacts import _Custody
from hermes_security.errors import VaultError
from hermes_security.migration_archive import read_backup_metadata, verified_tar

ROOT_PASSWORD = "synthetic-root-credential"
CHILD_PASSWORD = "synthetic-child-credential"


@pytest.fixture
def root(tmp_path, monkeypatch):
    home = tmp_path / "root"
    home.mkdir(mode=0o700)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    yield home
    vault.clear_vault_cache()


def _files(top: Path) -> dict[str, bytes]:
    return {p.relative_to(top).as_posix(): p.read_bytes() for p in top.rglob("*") if p.is_file()}


def _members(backup: Path, home: Path) -> dict[str, tuple[bytes, str | None]]:
    with verified_tar(backup, custody=_Custody(home)) as archive:
        return {m.name: (archive.extractfile(m).read(), m.pax_headers.get("HERMES.kind"))
                for m in archive if m.isfile()}


def _independent_vault(home: Path, nonce: bytes) -> None:
    """A real independently initialized profile: envelope, SQLCipher table, key slot, metadata copy."""
    from hermes_security.sqlite import connect

    vault.init_vault(home, CHILD_PASSWORD)
    child = vault.unlock(home, CHILD_PASSWORD)
    child.write_bytes(home / "config.yaml", b"model: child-" + nonce + b"\n", purpose="config")
    connection = connect(home / "state.db")
    try:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample VALUES (?)", (nonce.decode(),))
        connection.commit()
    finally:
        connection.close()
    _private, public = vault.generate_keypair()
    # The credential writer leaves its never-unlinked lock inode beside the metadata.
    vault.add_key_slot(home, public_key_raw=public, unlocked_vault=child)
    vault._atomic_write(home / ".hermes-vault.previous", (home / ".hermes-vault").read_bytes())


def test_root_migration_retains_child_islands_exactly_in_place_and_in_rollback(root):
    nonce = secrets.token_hex(16).encode()
    (root / "config.yaml").write_bytes(b"model: root-" + nonce + b"\n")
    work, plain, unknown = (root / "profiles" / name for name in ("work", "plain-child", "unclaimed-notes"))
    _independent_vault(work, nonce)
    plain.mkdir()
    (plain / "config.yaml").write_bytes(b"model: plain-" + nonce + b"\n")
    unknown.mkdir()
    (unknown / "notes.txt").write_bytes(b"retained " + nonce)
    vault.clear_vault_cache()  # the child stays locked: the root migration has no child key to use
    before = _files(root)

    report = migrate.migrate_home(root, ROOT_PASSWORD)

    assert report.ok, report.failures
    retained = {name: data for name, data in before.items() if name.startswith("profiles/")}
    assert {name: (root / name).read_bytes() for name in retained} == retained
    assert not (plain / ".hermes-vault").exists() and not (unknown / ".hermes-vault").exists()
    assert report.profiles_pending == ["profiles/plain-child"]
    assert {"profiles/work", "profiles/plain-child", "profiles/unclaimed-notes"} <= set(report.profiles_opaque)
    assert not report.backup_path.is_relative_to(root)
    members = _members(report.backup_path, root)
    # Exact payload: every original byte, child credential metadata included, never the live lock inode.
    expected = {name: data for name, data in before.items() if Path(name).name != ".hermes-vault.lock"}
    assert {name: data for name, (data, _kind) in members.items()} == expected
    assert members["profiles/work/.hermes-vault"][0] == before["profiles/work/.hermes-vault"]
    assert all(kind == "profile" for name, (_data, kind) in members.items() if name.startswith("profiles/"))
    assert vault.get_vault(root).read_bytes(root / "config.yaml", purpose="config") == before["config.yaml"]
    vault.clear_vault_cache()
    child = vault.unlock(work, CHILD_PASSWORD)  # never rekeyed: the child's own credential still opens it
    assert child.read_bytes(work / "config.yaml", purpose="config") == b"model: child-" + nonce + b"\n"
    from hermes_security.sqlite import connect

    connection = connect(work / "state.db")
    try:
        assert connection.execute("SELECT value FROM sample").fetchall() == [(nonce.decode(),)]
    finally:
        connection.close()


def test_named_home_beneath_vaulted_root_keeps_its_own_rollback_outside_the_root(root, tmp_path):
    nonce = secrets.token_hex(16).encode()
    vault.init_vault(root, ROOT_PASSWORD)
    vault.unlock(root, ROOT_PASSWORD).write_bytes(root / "config.yaml", b"model: root\n", purpose="config")
    child = root / "profiles" / "coder"
    (child / "logs").mkdir(parents=True)
    (child / "config.yaml").write_bytes(b"model: child-" + nonce + b"\n")
    (child / "logs" / "agent.log").write_bytes(b"line " + nonce + b"\n")
    with sqlite3.connect(child / "state.db") as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample VALUES (?)", (nonce.decode(),))
    connection.close()
    root_before = {name: (root / name).read_bytes() for name in (".hermes-vault", "config.yaml")}
    child_before = _files(child)
    vault.clear_vault_cache()

    report = migrate.migrate_home(child, CHILD_PASSWORD)

    assert report.ok, report.failures
    assert migration_profiles.storage_home(child) == root
    assert not report.backup_path.is_relative_to(root)
    assert report.backup_path.parent in {root.parent, root.parent / f"{root.name}-migration-backups"}
    assert nonce not in report.backup_path.read_bytes()
    assert {name: (root / name).read_bytes() for name in root_before} == root_before
    # The standalone rollback authenticates with the child's own credential alone.
    recovered = tmp_path / "isolated"
    recovered.mkdir(mode=0o700)
    vault._atomic_write(recovered / ".hermes-vault", read_backup_metadata(report.backup_path))
    vault.clear_vault_cache()
    vault.unlock(recovered, CHILD_PASSWORD)
    assert {name: data for name, (data, _kind) in _members(report.backup_path, recovered).items()} == child_before
    # Each island is inspected only through its own explicit unlock, never the root's key.
    vault.unlock(root, ROOT_PASSWORD)
    vault.unlock(child, CHILD_PASSWORD)
    assert migrate.scan_for_plaintext(root) == []


def _plant(case: str, root: Path) -> None:
    metadata = root / ".hermes-vault"
    if case == "symlink":
        elsewhere = root.parent / "elsewhere"
        elsewhere.mkdir(mode=0o700)
        vault.init_vault(elsewhere, ROOT_PASSWORD)
        metadata.symlink_to(elsewhere / ".hermes-vault")
    elif case == "directory":
        metadata.mkdir(mode=0o700)
    elif case == "malformed-json":
        vault._atomic_write(metadata, b"{not public vault metadata")
    else:
        vault.init_vault(root, ROOT_PASSWORD)
        if case == "unreadable":
            metadata.chmod(0)
        else:
            document = json.loads(metadata.read_bytes())
            document["kdf_n"] = 3
            vault._atomic_write(metadata, json.dumps(document).encode())


def _tree(top: Path) -> dict[str, tuple]:
    """Type and identity of every entry; a fence's empty lease is the only permitted addition."""
    tree = {}
    for directory, subdirectories, files in os.walk(top):
        for name in subdirectories + files:
            path = Path(directory) / name
            info = path.lstat()
            if name == "runtime.lock" and info.st_size == 0:
                continue
            identity = () if stat.S_ISDIR(info.st_mode) else (info.st_ino, info.st_size, info.st_mtime_ns)
            tree[path.relative_to(top).as_posix()] = (stat.S_IFMT(info.st_mode), *identity)
    return tree


@pytest.mark.parametrize("case", [
    "symlink", "directory", "malformed-json", "malformed-kdf",
    pytest.param("unreadable", marks=pytest.mark.skipif(os.geteuid() == 0, reason="root reads mode-000 files")),
])
def test_unsafe_ancestor_metadata_refuses_rollback_placement_without_changes(root, tmp_path, case):
    _plant(case, root)
    child = root / "profiles" / "coder"
    child.mkdir(parents=True)
    (child / "config.yaml").write_bytes(b"model: child\n")
    before = _tree(tmp_path)

    with pytest.raises(VaultError):
        migration_profiles.storage_home(child)
    with pytest.raises(VaultError):
        migrate.migrate_home(child, CHILD_PASSWORD)

    assert _tree(tmp_path) == before
    assert not (child / ".hermes-vault").exists()


def test_legacy_ancestor_sealed_profile_is_refused_before_new_metadata(root):
    from hermes_security import io

    vault.init_vault(root, ROOT_PASSWORD)
    owner = vault.unlock(root, ROOT_PASSWORD)
    child = root / "profiles" / "legacy"
    child.mkdir(parents=True)
    original = b"model: retained-legacy-profile\n"
    owner.write_bytes(child / "config.yaml", original, purpose="config")
    before = _files(child)
    assert io.read_bytes(child / "config.yaml", purpose="config") == original
    for dry in (True, False):
        with pytest.raises(VaultError, match="original ancestor vault"):
            migrate.migrate_home(child, CHILD_PASSWORD, dry_run=dry)
    assert not (child / ".hermes-vault").exists()
    assert all((child / name).read_bytes() == data for name, data in before.items())
    assert io.read_bytes(child / "config.yaml", purpose="config") == original
