"""Encrypted streaming migration rollback backups.

Contracts for ``hermes_security.migration_archive`` against a real initialized and unlocked vault
with real captured custody, real files and real tar streaming. Only the vault KDF cost is lowered.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import struct
import tarfile
from pathlib import Path

import pytest

from hermes_security import migration_archive as archive
from hermes_security import vault
from hermes_security.artifacts import _Custody

PASSWORD = "synthetic-migration-passphrase"
MIB = 1024 * 1024


@pytest.fixture
def home(tmp_path, monkeypatch):
    selected = tmp_path / "home"
    selected.mkdir(mode=0o700)
    selected = selected.resolve()
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(selected))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(selected, PASSWORD)
    vault.unlock(selected, PASSWORD)
    yield selected
    vault.clear_vault_cache()


def _tree(home: Path, canary: bytes) -> list:
    """A pre-migration home: a multi-record database, text, empty file, hard link and symlink."""
    (home / "sessions").mkdir(exist_ok=True)
    (home / "memories").mkdir(exist_ok=True)
    (home / "sessions" / "state.db").write_bytes(os.urandom(3 * MIB + 4321) + canary)
    (home / "memories" / "MEMORY.md").write_bytes(b"remember " + canary)
    (home / "memories" / "empty.txt").write_bytes(b"")
    os.link(home / "memories" / "MEMORY.md", home / "memories" / "alias.md")
    (home / "current").symlink_to("memories/MEMORY.md")
    attributes = {
        "sessions/state.db": {"kind": "sqlite", "sqlite_fingerprint": "synthetic-fingerprint"},
        "memories/MEMORY.md": {"kind": "file"},
        "memories/alias.md": {"kind": "file"},
        "memories/empty.txt": {},
        "current": {"kind": "symlink"},
    }
    entries = []
    for name, extra in attributes.items():
        path = home / name
        if not path.is_symlink():
            extra = {**extra, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        entries.append((path, Path(name), extra))
    return entries


def _expected(entries: list) -> dict:
    expected, first_inode = {}, {}
    for path, relative, attributes in entries:
        info = path.lstat()
        name = relative.as_posix()
        hermes = {"HERMES." + key: value for key, value in attributes.items()}
        if stat.S_ISLNK(info.st_mode):
            kind, link, content = tarfile.SYMTYPE, os.readlink(path), None
        elif info.st_ino in first_inode:
            kind, link, content = tarfile.LNKTYPE, first_inode[info.st_ino], None
        else:
            first_inode[info.st_ino] = name
            kind, link, content = tarfile.REGTYPE, "", path.read_bytes()
        expected[name] = (kind, link, stat.S_IMODE(info.st_mode), int(info.st_mtime), content, hermes)
    return expected


def _members(path: Path, custody) -> dict:
    seen = {}
    with archive.verified_tar(path, custody=custody) as tar:
        for member in tar:
            content = tar.extractfile(member).read() if member.isreg() else None
            hermes = {k: v for k, v in member.pax_headers.items() if k.startswith("HERMES.")}
            seen[member.name] = (member.type, member.linkname, member.mode, int(member.mtime), content, hermes)
    return seen


def test_backup_streams_tree_exactly_and_never_replaces_or_follows_links(home, tmp_path):
    canary = b"migration-canary-" + secrets.token_hex(16).encode()
    entries = _tree(home, canary)
    metadata = (home / ".hermes-vault").read_bytes()
    custody = _Custody(home)
    backups = tmp_path / "backups"
    backups.mkdir(mode=0o700)
    destination = backups / "rollback.hmb"

    summary = archive.write_backup(home, destination, custody=custody, entries=entries, metadata=metadata)

    published = destination.read_bytes()
    assert summary == {"bytes": len(published), "sha256": hashlib.sha256(published).hexdigest()}
    assert [p.name for p in backups.iterdir()] == ["rollback.hmb"]  # no stage remains
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert canary not in published and PASSWORD.encode() not in published
    assert archive.read_backup_metadata(destination) == metadata
    assert _members(destination, custody) == _expected(entries)

    with pytest.raises(archive.MigrationArchiveError, match="already exists"):
        archive.write_backup(home, destination, custody=custody, entries=entries, metadata=metadata)

    drifted = b"changed after the scan " + canary
    (home / "memories" / "MEMORY.md").write_bytes(drifted)
    with pytest.raises(archive.MigrationArchiveError, match="changed"):
        archive.write_backup(home, backups / "drifted.hmb", custody=custody, entries=entries, metadata=metadata)
    assert (home / "memories" / "MEMORY.md").read_bytes() == drifted

    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (outside / "secret").write_bytes(canary)
    (home / "linked").symlink_to(outside, target_is_directory=True)
    planted = [(home / "linked" / "secret", Path("linked/secret"), {})]
    with pytest.raises(archive.MigrationArchiveError, match="pinned directory"):
        archive.write_backup(home, backups / "planted.hmb", custody=custody, entries=planted, metadata=metadata)
    (tmp_path / "linked-backups").symlink_to(outside, target_is_directory=True)
    with pytest.raises(archive.MigrationArchiveError, match="real directory"):
        archive.write_backup(home, tmp_path / "linked-backups" / "x.hmb", custody=custody, entries=[],
                             metadata=metadata)

    assert [p.name for p in backups.iterdir()] == ["rollback.hmb"]
    assert destination.read_bytes() == published
    assert sorted(p.name for p in outside.iterdir()) == ["secret"]


def _layout(raw: bytes) -> tuple[bytes, list[bytes]]:
    offset = len(archive._MAGIC)
    (size,) = struct.unpack_from(">I", raw, offset)
    offset += 4 + size
    head, records = raw[:offset], []
    while offset < len(raw):
        (size,) = struct.unpack_from(">I", raw, offset)
        records.append(raw[offset:offset + 4 + size])
        offset += 4 + size
    return head, records


def _flipped(record: bytes) -> bytes:
    damaged = bytearray(record)
    damaged[len(damaged) // 2] ^= 0x01
    return bytes(damaged)


def _substituted_metadata(head: bytes) -> bytes:
    document = json.loads(head[len(archive._MAGIC) + 4:])
    document["metadata"] = base64.b64encode(b'{"substituted": true}').decode()
    body = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return archive._MAGIC + struct.pack(">I", len(body)) + body


DAMAGE = {
    "flipped-data": lambda h, r, oh, o: h + b"".join(r[:2] + [_flipped(r[2])] + r[3:]),
    "truncated-mid-frame": lambda h, r, oh, o: (h + b"".join(r))[:-7],
    "dropped-end": lambda h, r, oh, o: h + b"".join(r[:-1]),
    "dropped-data": lambda h, r, oh, o: h + b"".join(r[:2] + r[3:]),
    "reordered": lambda h, r, oh, o: h + b"".join([r[0], r[2], r[1]] + r[3:]),
    "trailing-garbage": lambda h, r, oh, o: h + b"".join(r) + b"\0" * 32,
    "replayed-end": lambda h, r, oh, o: h + b"".join(r) + r[-1],
    "spliced-from-other-backup": lambda h, r, oh, o: h + b"".join(r[:2] + [o[2]] + r[3:]),
    "header-substitution": lambda h, r, oh, o: oh + b"".join(r),
    "metadata-substitution": lambda h, r, oh, o: _substituted_metadata(h) + b"".join(r),
}


@pytest.mark.parametrize("damage", sorted(DAMAGE))
def test_damaged_backup_refuses_before_any_member_is_exposed(home, tmp_path, damage):
    entries = _tree(home, b"migration-canary-" + secrets.token_hex(16).encode())
    metadata = (home / ".hermes-vault").read_bytes()
    custody = _Custody(home)
    backups = tmp_path / "backups"
    backups.mkdir(mode=0o700)
    for name in ("first.hmb", "other.hmb"):
        archive.write_backup(home, backups / name, custody=custody, entries=entries, metadata=metadata)
    original = (backups / "first.hmb").read_bytes()
    head, records = _layout(original)
    other_head, other_records = _layout((backups / "other.hmb").read_bytes())
    assert len(records) >= 5 and len(other_records) >= 5  # header, several 1 MiB data records, end

    damaged = DAMAGE[damage](head, records, other_head, other_records)
    target = backups / "damaged.hmb"
    target.write_bytes(damaged)
    target.chmod(0o600)
    exposed = []
    with pytest.raises(archive.MigrationArchiveError):
        with archive.verified_tar(target, custody=custody) as tar:
            exposed.append(tar)

    assert exposed == []
    assert target.read_bytes() == damaged
    assert (backups / "first.hmb").read_bytes() == original
