"""Exact original-state admission for interrupted, explicitly resumed migration."""
from __future__ import annotations

import codecs
import hashlib
import re
import struct
from pathlib import Path

from hermes_security.errors import VaultIntegrityError

_BREAKS = re.compile(r"(\r\n|[\n\v\f\r\x1c-\x1e\x85\u2028\u2029])")
_LENGTH = struct.Struct(">Q")


class _LineDigest:
    def __init__(self, jsonl):
        self.jsonl = jsonl
        self.result = hashlib.sha256()
        self.line = hashlib.sha256()
        self.size = 0
        self.nonblank = False
        self.cr = False

    def add(self, text):
        data = text.encode("utf-8")
        self.line.update(data)
        self.size += len(data)
        self.nonblank |= bool(text.strip())

    def finish(self):
        if self.size and (not self.jsonl or self.nonblank):
            self.result.update(_LENGTH.pack(self.size) + self.line.digest())
        self.line, self.size, self.nonblank = hashlib.sha256(), 0, False

    def feed(self, text):
        if self.cr:
            if text.startswith("\n"):
                if not self.jsonl:
                    self.add("\n")
                text = text[1:]
            self.finish()
            self.cr = False
        parts = _BREAKS.split(text)
        for index, part in enumerate(parts):
            if index % 2 == 0:
                self.add(part)
            else:
                if not self.jsonl:
                    self.add(part)
                if part == "\r" and index == len(parts) - 2 and not parts[-1]:
                    self.cr = True
                else:
                    self.finish()


def plaintext_frames_fingerprint(path: Path) -> str:
    digest = _LineDigest(path.suffix == ".jsonl")
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            digest.feed(decoder.decode(chunk))
    digest.feed(decoder.decode(b"", final=True))
    digest.finish()
    return digest.result.hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def attributes(path: Path, relative: Path) -> dict:
    from hermes_security import migrate

    kind = migrate._classify(path, relative)
    values = {"kind": kind}
    if path.is_symlink():
        return values
    values["sha256"] = file_hash(path)
    if kind == "frames":
        values["frames_fingerprint"] = plaintext_frames_fingerprint(path)
    elif kind == "db":
        with path.open("rb") as source:
            if source.read(16) == b"SQLite format 3\0":
                values["sqlite_fingerprint"] = migrate._plain_dump_md5(path)
    return values


def _sealed_frames_fingerprint(path: Path, vault, purpose: str) -> str:
    from hermes_security import frames

    digest = hashlib.sha256()
    with path.open("rb") as source:
        while prefix := source.read(4):
            if len(prefix) != 4:
                raise VaultIntegrityError("Interrupted encrypted stream length")
            size = int.from_bytes(prefix, "big")
            if not 28 <= size <= 256 * 1024 * 1024:
                raise VaultIntegrityError("Encrypted frame exceeds the migration resume bound")
            raw = prefix + source.read(size)
            decoded = frames.decode_frame_at(raw, 0, vault=vault, purpose=purpose, strict=True)
            if decoded is None or decoded[1] != len(raw):
                raise VaultIntegrityError("Interrupted encrypted stream payload")
            payload = decoded[0]
            digest.update(_LENGTH.pack(len(payload)) + hashlib.sha256(payload).digest())
    return digest.hexdigest()


def admitted(path: Path, relative: Path, original: dict, vault, *, checkpoint=None) -> bool:
    """False means exact original plaintext, True means verified converted content; anything else refuses."""
    from hermes_security import migrate

    if path.is_symlink() or not path.is_file():
        raise VaultIntegrityError(f"Migration source changed type or disappeared: {relative}")
    if file_hash(path) == original.get("sha256"):
        return False
    kind = original.get("kind")
    if kind == "db":
        return _database_admission(path, relative, original, checkpoint)
    elif kind == "frames":
        actual = _sealed_frames_fingerprint(path, vault, migrate._frame_purpose(path))
        expected = original.get("frames_fingerprint")
    else:
        content = vault.read_bytes(path, purpose=migrate._purpose_for(relative))
        actual, expected = hashlib.sha256(content).hexdigest(), original.get("sha256")
    if not expected or actual != expected:
        raise VaultIntegrityError(f"Migration content changed after the verified rollback: {relative}")
    return True


def _database_admission(path, relative, original, checkpoint):
    from contextlib import closing
    from hermes_security import migrate
    from hermes_security.sqlite import connect

    expected = original.get("sqlite_fingerprint")
    with path.open("rb") as source:
        plaintext = source.read(16) == b"SQLite format 3\0"
    if plaintext:
        # WAL checkpointing may change physical bytes before ciphertext replaces the
        # original. Only this exact in-progress entry may admit equivalent SQLite.
        active = {"path": relative.as_posix(), "kind": "db", "original": original.get("sha256")}
        if checkpoint != active or not expected or migrate._plain_dump_md5(path) != expected:
            raise VaultIntegrityError(f"Original SQLite content changed during migration: {relative}")
        return False
    with closing(connect(path)) as connection:
        actual = migrate._conn_fingerprint(connection)
    if not expected or actual != expected:
        raise VaultIntegrityError(f"Converted SQLite content changed during migration: {relative}")
    return True
