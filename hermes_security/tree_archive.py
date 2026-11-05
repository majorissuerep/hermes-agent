"""Authenticated encrypted archives of one owned directory tree (the shared record engine).

Concrete domains (``browser_profile_archive``, ``browser_worker_archive``) choose the archive
location, the frames purpose, the header slot/identity binding and a :class:`TreePolicy`; this
module only streams, verifies and restores. It never chooses a path or a key purpose itself.

Wire format: the ``hermes_security.frames`` stream format, with the key and associated data bound to
the caller's exact purpose. Each frame payload is one record
``type (1) | archive id (16) | sequence (8)`` + body:

- ``H`` header: format, slot, identity (with its keyed digest)
- ``D`` directory, ``F`` file start (path, size, mtime), ``C`` file bytes
- ``E`` end: entry count, plaintext bytes, tree digest — must be the final record

A random per-archive id and strict sequence numbers stop reordering, splicing between generations,
and truncation. A reader rejects anything else before any restored tree is published.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import stat
import struct
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable, Iterator

from hermes_security import frames
from hermes_security.errors import VaultError, VaultLockedError

_CHUNK = 1024 * 1024
_MAX_META = 16 * 1024
_FORMAT = 1
_RECORD = struct.Struct(">c16sQ")
_LENGTH = struct.Struct(">I")
_MIN_FRAME = 12 + 16
_MAX_FRAME = _CHUNK + _RECORD.size + _MIN_FRAME
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
# Published in every format-1 archive's E record; kept for all domains so the format stays one.
_TREE_DIGEST_LABEL = b"hermes-browser-profile-tree|v1"


class ArchiveError(VaultError):
    """Tree persistence or restore failed; the message identifies publication state."""


@dataclass(frozen=True)
class ArchiveSummary:
    entries: int
    bytes: int
    digest: str


@dataclass(frozen=True)
class TreePolicy:
    """One domain's bounds and retention rule over the shared format.

    ``excluded(relative, kind)`` drops a known expendable entry before its type is checked;
    ``None`` retains every regular file and directory. ``label`` names the domain in errors.
    ``preserve_owner_exec`` declares authenticated owner-execute metadata and includes it in the
    completion digest. Domains without it keep the original format-1 bytes and private restore.
    """

    label: str
    max_bytes: int
    max_entries: int
    excluded: Callable[[str, int], bool] | None = None
    bound_hint: str = ""
    preserve_owner_exec: bool = False


def canonical_json(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


# --- Tree walking and digests -----------------------------------------------------------------

@contextlib.contextmanager
def _source_directory(root: Path | int, label: str, relative: str = ""):
    """Pin every source ancestor without following links or changing legacy modes."""
    flags = os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW | _CLOEXEC
    fd = os.dup(root) if isinstance(root, int) else os.open(root, flags)
    try:
        for part in PurePosixPath(relative).parts:
            if part in {"..", "/"}:
                raise ArchiveError(f"A {label} entry left its source directory")
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _directory_entries(root: Path | int, label: str, relative: str) -> list[tuple[str, int]]:
    with _source_directory(root, label, relative) as parent:
        with os.scandir(parent) as listing:
            return sorted((entry.name, stat.S_IFMT(entry.stat(follow_symlinks=False).st_mode))
                          for entry in listing)


def _iter_tree(root: Path | int, policy: TreePolicy) -> Iterator[tuple[str, bool]]:
    """``(relative path, is_dir)`` in deterministic order; directories precede their contents.

    Only the policy's declared exclusions are skipped. An unexpected link or special file refuses
    persistence so verified legacy repair cannot erase it.
    """
    pending = [""]
    while pending:
        relative = pending.pop()
        children = []
        for name, kind in _directory_entries(root, policy.label, relative):
            child = f"{relative}/{name}" if relative else name
            if policy.excluded is not None and policy.excluded(child, kind):
                continue
            if kind == stat.S_IFDIR:
                children.append(child)
                yield child, True
            elif kind == stat.S_IFREG:
                yield child, False
            else:
                raise ArchiveError(f"Unsupported {policy.label} entry {child!r}; every existing copy was retained")
        pending.extend(reversed(children))


class _TreeDigest:
    def __init__(self, preserve_owner_exec: bool = False) -> None:
        self._hash = hashlib.sha256(_TREE_DIGEST_LABEL)
        self._owner_exec = preserve_owner_exec

    def directory(self, relative: str) -> None:
        self._hash.update(b"D" + os.fsencode(relative) + b"\0")

    def file(self, relative: str, size: int, content: bytes, owner_exec: bool = False) -> None:
        self._hash.update(b"F" + os.fsencode(relative) + b"\0" + size.to_bytes(8, "big") + content)
        if self._owner_exec:
            self._hash.update(b"X" + bytes([owner_exec]))

    def hexdigest(self) -> str:
        return self._hash.hexdigest()


@contextlib.contextmanager
def _source_file(root: Path | int, label: str, relative: str, preserve_owner_exec: bool = False):
    path = PurePosixPath(_relative(relative, label))
    with _source_directory(root, label, path.parent.as_posix()) as parent:
        fd = os.open(path.name, os.O_RDONLY | _NOFOLLOW | _CLOEXEC | getattr(os, "O_NONBLOCK", 0), dir_fd=parent)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ArchiveError(f"A {label} entry changed type while it was persisted")
        yield handle, info
        after = os.fstat(handle.fileno())
        exec_changed = preserve_owner_exec and bool((after.st_mode ^ info.st_mode) & stat.S_IXUSR)
        if handle.read(1) or after.st_mtime_ns != info.st_mtime_ns or exec_changed:
            raise ArchiveError(f"The {label} changed while it was persisted (a browser is still writing it)")


def _read_chunks(handle: BinaryIO, size: int, label: str) -> Iterator[bytes]:
    remaining = size
    while remaining:
        chunk = handle.read(min(_CHUNK, remaining))
        if not chunk:
            raise ArchiveError(f"The {label} changed while it was persisted (a browser is still writing it)")
        remaining -= len(chunk)
        yield chunk


def tree_digest(root: Path, policy: TreePolicy) -> ArchiveSummary:
    """Digest of exactly the entries an archive of ``root`` under ``policy`` would hold."""
    with _source_directory(root, policy.label) as pinned:
        return _tree_digest(pinned, policy)


def _tree_digest(root: int, policy: TreePolicy) -> ArchiveSummary:
    digest, entries, total = _TreeDigest(policy.preserve_owner_exec), 0, 0
    for relative, is_dir in _iter_tree(root, policy):
        entries += 1
        if is_dir:
            digest.directory(relative)
            continue
        with _source_file(root, policy.label, relative, policy.preserve_owner_exec) as (handle, info):
            content = hashlib.sha256()
            for chunk in _read_chunks(handle, info.st_size, policy.label):
                content.update(chunk)
        digest.file(relative, info.st_size, content.digest(), bool(info.st_mode & stat.S_IXUSR))
        total += info.st_size
    return ArchiveSummary(entries, total, digest.hexdigest())


# --- Writing ----------------------------------------------------------------------------------

class _PurposeKey:
    """``frames`` vault adapter: one custody-checked derivation reused for one archive pass."""

    def __init__(self, custody, purpose: str) -> None:
        self._label = "frames:" + purpose
        self._key = custody.derive_key(self._label)

    def derive_key(self, label: str) -> bytes:
        if label != self._label:
            raise VaultLockedError("An archive key is bound to one archive path")
        return self._key


class _RecordSink:
    def __init__(self, fd: int, keyed: _PurposeKey, purpose: str) -> None:
        self.fd, self.keyed, self.purpose = fd, keyed, purpose
        self.archive_id = secrets.token_bytes(16)
        self.sequence = 0

    def record(self, kind: bytes, body: bytes) -> None:
        payload = _RECORD.pack(kind, self.archive_id, self.sequence) + body
        from hermes_security.artifacts import _write_all

        _write_all(self.fd, frames.encode_frames([payload], vault=self.keyed, purpose=self.purpose))
        self.sequence += 1


def _meta(document: dict, label: str) -> bytes:
    body = canonical_json(document)
    if len(body) > _MAX_META:
        raise ArchiveError(f"A {label} entry name is too long to persist")
    return body


def _stream_file(sink: _RecordSink, root: int, relative: str, digest: _TreeDigest, budget: int,
                 policy: TreePolicy) -> int:
    with _source_file(root, policy.label, relative, policy.preserve_owner_exec) as (handle, info):
        if info.st_size > budget:
            raise ArchiveError(
                f"The {policy.label} exceeds the {policy.max_bytes // 1024 ** 3} GiB persistence bound; "
                f"nothing was discarded.{policy.bound_hint}")
        metadata = {"path": relative, "size": info.st_size, "mtime_ns": info.st_mtime_ns}
        if policy.preserve_owner_exec:
            metadata["owner_exec"] = bool(info.st_mode & stat.S_IXUSR)
        sink.record(b"F", _meta(metadata, policy.label))
        content = hashlib.sha256()
        for chunk in _read_chunks(handle, info.st_size, policy.label):
            content.update(chunk)
            sink.record(b"C", chunk)
    digest.file(relative, info.st_size, content.digest(), bool(info.st_mode & stat.S_IXUSR))
    return info.st_size


def _stream_tree(sink: _RecordSink, root: int, header: dict, policy: TreePolicy) -> ArchiveSummary:
    label = policy.label
    if policy.preserve_owner_exec:
        header = {**header, "file_attributes": "owner-exec-v1"}
    sink.record(b"H", _meta({"format": _FORMAT, **header}, label))
    digest, entries, total = _TreeDigest(policy.preserve_owner_exec), 0, 0
    for relative, is_dir in _iter_tree(root, policy):
        entries += 1
        if entries > policy.max_entries:
            raise ArchiveError(f"The {label} has more than {policy.max_entries} entries; nothing was discarded")
        if is_dir:
            sink.record(b"D", _meta({"path": relative}, label))
            digest.directory(relative)
        else:
            total += _stream_file(sink, root, relative, digest, policy.max_bytes - total, policy)
    summary = ArchiveSummary(entries, total, digest.hexdigest())
    sink.record(b"E", _meta({"entries": entries, "bytes": total, "digest": summary.digest}, label))
    return summary


def write_archive(home: Path, relative: PurePosixPath, source: Path, *, custody, purpose: str,
                  policy: TreePolicy, slot: str, identity: dict, digest: str) -> ArchiveSummary:
    """Stream ``source`` into an owner-only ciphertext stage, authenticate it, then publish atomically.

    The caller has already admitted ``relative`` and chosen its concrete ``purpose``.
    Pre-publication failures leave the previous archive and source untouched.
    A post-publication fsync failure explicitly reports uncertain durability;
    the new authenticated archive and active source are retained.
    """
    from hermes_security.artifacts import _parent

    home, relative = Path(home), PurePosixPath(relative)
    keyed = _PurposeKey(custody, purpose)
    header = {"slot": slot, "identity": identity, "identity_digest": digest}
    with _parent(home, Path(relative), create=True) as parent:
        stage = ".stage-" + secrets.token_hex(16)
        fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC, 0o600, dir_fd=parent)
        try:
            try:
                with _source_directory(source, policy.label) as pinned:
                    summary = _stream_tree(_RecordSink(fd, keyed, purpose), pinned, header, policy)
                    before, after = os.fstat(pinned), os.stat(source, follow_symlinks=False)
                    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                        raise ArchiveError(f"The {policy.label} source directory was replaced; "
                                           "every copy was retained")
                os.fsync(fd)
            finally:
                os.close(fd)
            if _replay_from(parent, stage, keyed, purpose, _Replay(policy, slot, digest, None)) != summary:
                raise ArchiveError(f"The sealed {policy.label} did not authenticate before publication")
            custody.check()
            os.replace(stage, relative.name, src_dir_fd=parent, dst_dir_fd=parent)
            try:
                os.fsync(parent)
            except OSError as exc:
                raise ArchiveError("The new encrypted browser archive was published but its durability "
                                   "could not be confirmed; keep the active RAM copy and retry saving") from exc
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(stage, dir_fd=parent)
    return summary


# --- Reading ----------------------------------------------------------------------------------

def _wire_bound(policy: TreePolicy) -> int:
    frames_max = 2 * policy.max_entries + policy.max_bytes // _CHUNK + 4
    return policy.max_bytes + frames_max * (_MAX_META + _RECORD.size + _MIN_FRAME + _LENGTH.size)


def _read_exact(handle: BinaryIO, size: int, label: str, *, eof_ok: bool = False) -> bytes | None:
    data = handle.read(size)
    if eof_ok and not data:
        return None
    if len(data) != size:
        raise ArchiveError(f"The {label} archive is incomplete")
    return data


def _iter_records(handle: BinaryIO, keyed: _PurposeKey, purpose: str, label: str) -> Iterator[tuple[bytes, bytes]]:
    archive_id, sequence = None, 0
    while (prefix := _read_exact(handle, _LENGTH.size, label, eof_ok=True)) is not None:
        (size,) = _LENGTH.unpack(prefix)
        if not _MIN_FRAME <= size <= _MAX_FRAME + _MAX_META:
            raise ArchiveError(f"The {label} archive is corrupt")
        decoded = frames.decode_frame_at(prefix + _read_exact(handle, size, label), 0, vault=keyed,
                                         purpose=purpose, strict=True)
        if decoded is None or len(decoded[0]) < _RECORD.size:
            raise ArchiveError(f"The {label} archive is corrupt")
        kind, record_id, record_sequence = _RECORD.unpack_from(decoded[0])
        archive_id = record_id if archive_id is None else archive_id
        if record_id != archive_id or record_sequence != sequence:
            raise ArchiveError(f"The {label} archive records are out of order")
        sequence += 1
        yield kind, decoded[0][_RECORD.size:]


def _relative(text: Any, label: str) -> str:
    if not isinstance(text, str) or not text or "\x00" in text or len(text) > 4096:
        raise ArchiveError(f"The {label} archive names an invalid entry")
    if any(part in ("", ".", "..") for part in text.split("/")):
        raise ArchiveError(f"The {label} archive names an invalid entry")
    return text


def _document(body: bytes, label: str) -> dict:
    try:
        document = json.loads(body)
    except ValueError as exc:
        raise ArchiveError(f"The {label} archive is corrupt") from exc
    if not isinstance(document, dict):
        raise ArchiveError(f"The {label} archive is corrupt")
    return document


class _Replay:
    """Validate the record grammar; with a target, materialize into that fresh directory."""

    def __init__(self, policy: TreePolicy, slot: str | None, digest: str | None, target: Path | None) -> None:
        self.policy, self.label = policy, policy.label
        self.slot, self.expected, self.target = slot, digest, target
        self.header: dict | None = None
        self.summary: ArchiveSummary | None = None
        self.tree, self.entries, self.total = _TreeDigest(policy.preserve_owner_exec), 0, 0
        self.pending: list | None = None  # [relative, remaining, fd, content hash, mtime_ns, size, owner_exec]

    def feed(self, kind: bytes, body: bytes) -> None:
        handler = {b"H": self._header, b"D": self._directory, b"F": self._file,
                   b"C": self._chunk, b"E": self._end}.get(kind)
        if handler is None or self.summary is not None:
            raise ArchiveError(f"The {self.label} archive is corrupt")
        handler(body)

    def _require_entries(self) -> None:
        if self.header is None or self.pending is not None:
            raise ArchiveError(f"The {self.label} archive is corrupt")

    def _header(self, body: bytes) -> None:
        if self.header is not None:
            raise ArchiveError(f"The {self.label} archive is corrupt")
        header = _document(body, self.label)
        if header.get("format") != _FORMAT:
            raise ArchiveError(f"The {self.label} archive uses an unsupported format")
        if self.policy.preserve_owner_exec and header.get("file_attributes") != "owner-exec-v1":
            raise ArchiveError(f"The {self.label} archive lacks authenticated file attributes")
        if self.slot is not None and (header.get("slot") != self.slot
                                      or header.get("identity_digest") != self.expected):
            raise ArchiveError(f"The {self.label} archive belongs to a different profile or account")
        self.header = header

    def _count(self) -> None:
        self.entries += 1
        if self.entries > self.policy.max_entries:
            raise ArchiveError(f"The {self.label} archive exceeds its entry bound")

    def _directory(self, body: bytes) -> None:
        self._require_entries()
        relative = _relative(_document(body, self.label).get("path"), self.label)
        self._count()
        if self.target is not None:
            os.mkdir(self.target / relative, 0o700)
        self.tree.directory(relative)

    def _file(self, body: bytes) -> None:
        self._require_entries()
        document = _document(body, self.label)
        relative = _relative(document.get("path"), self.label)
        size, mtime = document.get("size"), document.get("mtime_ns")
        if (not isinstance(size, int) or size < 0 or size > self.policy.max_bytes - self.total
                or not isinstance(mtime, int)):
            raise ArchiveError(f"The {self.label} archive is corrupt")
        owner_exec = document.get("owner_exec", False) if self.policy.preserve_owner_exec else False
        if type(owner_exec) is not bool or (self.policy.preserve_owner_exec and "owner_exec" not in document):
            raise ArchiveError(f"The {self.label} archive has invalid file attributes")
        self._count()
        fd = -1
        if self.target is not None:
            fd = os.open(self.target / relative, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC, 0o600)
        self.pending = [relative, size, fd, hashlib.sha256(), mtime, size, owner_exec]
        if size == 0:
            self._finish_file()

    def _chunk(self, body: bytes) -> None:
        if self.pending is None or not body or len(body) > self.pending[1]:
            raise ArchiveError(f"The {self.label} archive is corrupt")
        if self.pending[2] >= 0:
            from hermes_security.artifacts import _write_all

            _write_all(self.pending[2], body)
        self.pending[3].update(body)
        self.pending[1] -= len(body)
        if self.pending[1] == 0:
            self._finish_file()

    def _finish_file(self) -> None:
        relative, _remaining, fd, content, mtime, size, owner_exec = self.pending
        self.pending = None
        if fd >= 0:
            try:
                if os.utime in os.supports_fd:
                    os.utime(fd, ns=(mtime, mtime))
                if self.policy.preserve_owner_exec:
                    os.fchmod(fd, 0o700 if owner_exec else 0o600)
            finally:
                os.close(fd)
        self.tree.file(relative, size, content.digest(), owner_exec)
        self.total += size

    def _end(self, body: bytes) -> None:
        self._require_entries()
        document = _document(body, self.label)
        summary = ArchiveSummary(self.entries, self.total, self.tree.hexdigest())
        if (document.get("entries"), document.get("bytes"), document.get("digest")) != (
                summary.entries, summary.bytes, summary.digest):
            raise ArchiveError(f"The {self.label} archive failed its content verification")
        self.summary = summary

    def close(self) -> None:
        if self.pending is not None and self.pending[2] >= 0:
            os.close(self.pending[2])
        self.pending = None


def _replay_from(parent: int, name: str, keyed: _PurposeKey, purpose: str, replay: _Replay) -> ArchiveSummary:
    fd = os.open(name, os.O_RDONLY | _NOFOLLOW | _CLOEXEC | getattr(os, "O_NONBLOCK", 0), dir_fd=parent)
    with os.fdopen(fd, "rb", buffering=_CHUNK) as handle:
        return _replay_handle(handle, keyed, purpose, replay)


def _replay_handle(handle, keyed: _PurposeKey, purpose: str, replay: _Replay) -> ArchiveSummary:
    label = replay.label
    info = os.fstat(handle.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError(f"A {label} archive must be an owner-only regular file")
    if info.st_size > _wire_bound(replay.policy):
        raise ArchiveError(f"The {label} archive exceeds its supported bound")
    try:
        for kind, body in _iter_records(handle, keyed, purpose, label):
            replay.feed(kind, body)
    finally:
        replay.close()
    if replay.summary is None:
        raise ArchiveError(f"The {label} archive is incomplete")
    return replay.summary


def authenticate_ciphertext(handle, *, custody, purpose: str, policy: TreePolicy,
                            slot: str | None = None, digest: str | None = None) -> ArchiveSummary:
    """Authenticate a caller-owned opened archive completely; restore no plaintext or close its FD."""
    summary = _replay_handle(handle, _PurposeKey(custody, purpose), purpose, _Replay(policy, slot, digest, None))
    custody.check()
    return summary


def replay_opened(handle, *, custody, purpose: str, policy: TreePolicy,
                  slot: str | None, digest: str | None, target: Path | None = None) -> ArchiveSummary:
    """Replay one caller-pinned FD through the same complete authenticated reader."""
    summary = _replay_handle(handle, _PurposeKey(custody, purpose), purpose,
                             _Replay(policy, slot, digest, target))
    custody.check()
    return summary


def replay_archive(home: Path, relative: PurePosixPath, *, custody, purpose: str, policy: TreePolicy,
                   slot: str | None, digest: str | None, target: Path | None) -> ArchiveSummary:
    """Authenticate one already admitted archive end to end; with ``target`` restore into it.

    ``slot``/``digest`` bind the restore to the exact owner slot and source identity (``slot=None``
    checks only authenticity and grammar). The caller publishes ``target`` only after this returns.
    """
    from hermes_security.artifacts import _parent

    relative = PurePosixPath(relative)
    keyed = _PurposeKey(custody, purpose)
    with _parent(Path(home), Path(relative)) as parent:
        summary = _replay_from(parent, relative.name, keyed, purpose, _Replay(policy, slot, digest, target))
    custody.check()
    return summary


def ciphertext_size(home: Path, relative: PurePosixPath) -> int | None:
    try:
        return os.stat(Path(home) / Path(relative), follow_symlinks=False).st_size
    except FileNotFoundError:
        return None
