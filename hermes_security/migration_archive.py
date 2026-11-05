"""Encrypted, streaming pre-migration rollback backups.

A migration backup holds a whole home's pre-migration bytes, so it is sealed as it is streamed: the
tar stream (``mode="w|"``) goes straight into authenticated 1 MiB records and no plaintext tar or
whole-file buffer ever exists. This is a dedicated rollback container, not a model artifact or a
general archive format.

Wire format::

    magic | u32 BE header length | canonical JSON {"backup_id", "format", "metadata"} | frames...

The header is public: a random backup id and the exact public ``.hermes-vault`` bytes (base64), so the
intended original credential can open a standalone backup. Every following frame is a
``hermes_security.frames`` frame whose purpose binds the backup id and the header's SHA-256. Each
payload is ``type (1) | backup id (16) | sequence (8)`` + body:

- ``H`` header digest (first), ``D`` 1 B-1 MiB of tar stream, ``E`` total plaintext bytes and SHA-256
  (mandatory, final, followed by end of file).

Readers bound each frame length before reading it and authenticate the complete file before any tar
member is exposed, so truncation, reordering, splicing, header substitution and trailing data refuse.
"""
from __future__ import annotations

import base64
import contextlib
import errno
import grp
import hashlib
import json
import os
import pwd
import secrets
import stat
import struct
import tarfile
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator

from hermes_security import frames
from hermes_security.artifacts import _write_all
from hermes_security.errors import VaultError, VaultIntegrityError

_MAGIC = b"HERMES-MIGRATION-BACKUP\x00"
_FORMAT = 1
_CHUNK = 1024 * 1024
_MAX_METADATA = 1024 * 1024
_MAX_HEADER = 4 * ((_MAX_METADATA + 2) // 3) + 256
_HEADER_LENGTH = struct.Struct(">I")
_RECORD = struct.Struct(">c16sQ")
_END = struct.Struct(">Q32s")
_SEAL = frames._FRAME_OVERHEAD - frames._LENGTH.size
_MIN_FRAME = _SEAL + _RECORD.size + 1
_MAX_FRAME = _SEAL + _RECORD.size + _CHUNK
_ATTRIBUTES = frozenset({"kind", "sha256", "sqlite_fingerprint", "frames_fingerprint"})
_MAX_ATTRIBUTE = 4096
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


class MigrationArchiveError(VaultError):
    """A migration backup could not be written or did not authenticate; originals are retained."""


def _corrupt(detail: str = "is corrupt") -> MigrationArchiveError:
    return MigrationArchiveError(f"The encrypted migration backup {detail}")


def _changed() -> MigrationArchiveError:
    return MigrationArchiveError("The encrypted migration backup changed while it was read")


def _drift(relative: Path) -> MigrationArchiveError:
    return MigrationArchiveError(
        f"Migration source {relative.as_posix()!r} changed or left its pinned directory while it was "
        "backed up; every original was retained")


# --- Header ------------------------------------------------------------------------------------

def _canonical(document: dict) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _check_metadata(metadata) -> bytes:
    if not isinstance(metadata, (bytes, bytearray)) or not 0 < len(metadata) <= _MAX_METADATA:
        raise MigrationArchiveError("Migration backup vault metadata must be 1 byte to 1 MiB")
    try:
        document = json.loads(metadata)
    except ValueError as exc:
        raise MigrationArchiveError("Migration backup vault metadata is not public vault metadata") from exc
    if not isinstance(document, dict):
        raise MigrationArchiveError("Migration backup vault metadata is not public vault metadata")
    return bytes(metadata)


def _encode_header(backup_id: bytes, metadata: bytes) -> bytes:
    return _canonical({"backup_id": backup_id.hex(), "format": _FORMAT,
                       "metadata": base64.b64encode(metadata).decode("ascii")})


def _decode_header(raw: bytes) -> tuple[bytes, bytes]:
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise _corrupt() from exc
    if not isinstance(document, dict) or set(document) != {"backup_id", "format", "metadata"}:
        raise _corrupt()
    if document["format"] != _FORMAT:
        raise _corrupt("uses an unsupported format")
    if not isinstance(document["backup_id"], str) or not isinstance(document["metadata"], str):
        raise _corrupt()
    try:
        backup_id = bytes.fromhex(document["backup_id"])
        metadata = base64.b64decode(document["metadata"], validate=True)
    except ValueError as exc:
        raise _corrupt() from exc
    # Only the exact canonical encoding is admitted; its digest is bound into every frame key.
    if len(backup_id) != 16 or _encode_header(backup_id, metadata) != raw:
        raise _corrupt()
    return backup_id, _check_metadata(metadata)


def _purpose(backup_id: bytes, header: bytes) -> str:
    return f"migration-rollback:v{_FORMAT}:{backup_id.hex()}:{hashlib.sha256(header).hexdigest()}"


# --- Writing -----------------------------------------------------------------------------------

class _Sink:
    """Tar stream target: seals each full 1 MiB of tar bytes into the stage as it arrives."""

    def __init__(self, fd: int, custody, header: bytes, backup_id: bytes) -> None:
        self.fd, self.custody, self.backup_id = fd, custody, backup_id
        self.purpose = _purpose(backup_id, header)
        self.sequence = 0
        self.pending = bytearray()
        self.total, self.plain = 0, hashlib.sha256()
        self.size, self.digest = 0, hashlib.sha256()
        self._raw(_MAGIC + _HEADER_LENGTH.pack(len(header)) + header)
        self._record(b"H", hashlib.sha256(header).digest())

    def _raw(self, data: bytes) -> None:
        _write_all(self.fd, data)
        self.digest.update(data)
        self.size += len(data)

    def _record(self, kind: bytes, body: bytes) -> None:
        # The original custody checks liveness and derives the key for every sealed record.
        payload = _RECORD.pack(kind, self.backup_id, self.sequence) + body
        self._raw(frames.encode_frames([payload], vault=self.custody, purpose=self.purpose))
        self.sequence += 1

    def write(self, data) -> int:
        self.pending += data
        self.total += len(data)
        self.plain.update(data)
        while len(self.pending) >= _CHUNK:
            self._record(b"D", bytes(self.pending[:_CHUNK]))
            del self.pending[:_CHUNK]
        return len(data)

    def finish(self) -> None:
        if self.pending:
            self._record(b"D", bytes(self.pending))
            self.pending.clear()
        self._record(b"E", _END.pack(self.total, self.plain.digest()))


class _Hashing:
    def __init__(self, handle: BinaryIO) -> None:
        self.handle, self.hash, self.size = handle, hashlib.sha256(), 0

    def read(self, size: int = -1) -> bytes:
        data = self.handle.read(size)
        self.hash.update(data)
        self.size += len(data)
        return data


def _pax_headers(attributes: dict) -> dict[str, str]:
    if not set(attributes) <= _ATTRIBUTES:
        raise ValueError("Unsupported migration backup entry attribute")
    for value in attributes.values():
        if not isinstance(value, str) or not value or len(value) > _MAX_ATTRIBUTE or "\x00" in value:
            raise ValueError("Migration backup entry attributes must be short non-empty text")
    digest = attributes.get("sha256")
    if digest is not None and (len(digest) != 64 or digest.strip("0123456789abcdef")):
        raise ValueError("A migration backup sha256 attribute must be lowercase hexadecimal")
    return {"HERMES." + key: value for key, value in sorted(attributes.items())}


def _relative(home: Path, source_path, relative_path) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or not relative.parts or any(part in (".", "..") for part in relative.parts):
        raise ValueError("A migration backup entry must be home-relative")
    if Path(source_path) != home / relative:
        raise ValueError("A migration backup entry must name its own home-relative source")
    return relative


@contextlib.contextmanager
def _source_parent(home_fd: int, relative: Path) -> Iterator[int]:
    """Pin each source ancestor below the home; a planted link refuses instead of being followed."""
    fd = os.dup(home_fd)
    try:
        for part in relative.parts[:-1]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW | _CLOEXEC, dir_fd=fd)
            except OSError as exc:
                raise _drift(relative) from exc
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _lstat(parent: int, relative: Path) -> os.stat_result:
    try:
        return os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
    except OSError as exc:
        raise _drift(relative) from exc


def _add_regular(tar: tarfile.TarFile, parent: int, relative: Path, info, headers: dict) -> None:
    try:
        fd = os.open(relative.name, os.O_RDONLY | _NOFOLLOW | _CLOEXEC | _NONBLOCK, dir_fd=parent)
    except OSError as exc:
        raise _drift(relative) from exc
    with os.fdopen(fd, "rb") as handle:
        pinned = os.fstat(fd)
        if (pinned.st_dev, pinned.st_ino) != (info.st_dev, info.st_ino) or not stat.S_ISREG(pinned.st_mode):
            raise _drift(relative)
        # Same member metadata (including hard links) as tar.add(recursive=False), from the pinned FD.
        member = tar.gettarinfo(arcname=relative.as_posix(), fileobj=handle)
        member.pax_headers = headers
        content = _Hashing(handle)
        tar.addfile(member, content if member.type == tarfile.REGTYPE else None)
        while content.read(_CHUNK):
            pass  # growth is drift; a hard-link member still has its content digest verified
        after = os.fstat(fd)
    expected = headers.get("HERMES.sha256")
    if ((content.size, after.st_size, after.st_mtime_ns) != (pinned.st_size, pinned.st_size, pinned.st_mtime_ns)
            or expected not in (None, content.hash.hexdigest())):
        raise _drift(relative)


def _add_special(tar: tarfile.TarFile, parent: int, relative: Path, info, headers: dict) -> None:
    if "HERMES.sha256" in headers:
        raise _drift(relative)  # a content digest was recorded for what was then a regular file
    member = tar.tarinfo(relative.as_posix())
    member.mode, member.uid, member.gid, member.mtime = info.st_mode, info.st_uid, info.st_gid, info.st_mtime
    with contextlib.suppress(KeyError):
        member.uname = pwd.getpwuid(info.st_uid).pw_name
    with contextlib.suppress(KeyError):
        member.gname = grp.getgrgid(info.st_gid).gr_name
    if stat.S_ISLNK(info.st_mode):
        try:
            member.type, member.linkname = tarfile.SYMTYPE, os.readlink(relative.name, dir_fd=parent)
        except OSError as exc:
            raise _drift(relative) from exc
    else:
        member.type = tarfile.DIRTYPE
    after = _lstat(parent, relative)
    if (after.st_ino, after.st_mode, after.st_mtime_ns) != (info.st_ino, info.st_mode, info.st_mtime_ns):
        raise _drift(relative)
    member.pax_headers = headers
    tar.addfile(member)


def _add_entry(tar: tarfile.TarFile, home_fd: int, relative: Path, attributes: dict) -> None:
    headers = _pax_headers(attributes)
    with _source_parent(home_fd, relative) as parent:
        info = _lstat(parent, relative)
        if stat.S_ISREG(info.st_mode):
            _add_regular(tar, parent, relative, info, headers)
        elif stat.S_ISLNK(info.st_mode) or stat.S_ISDIR(info.st_mode):
            _add_special(tar, parent, relative, info, headers)
        else:
            raise MigrationArchiveError(f"Migration source {relative.as_posix()!r} has an unsupported type")


def _open_home(home: Path, custody) -> int:
    selected = home.expanduser().resolve()
    if custody.home != selected:
        raise PermissionError("Migration backup custody belongs to a different profile")
    custody.check()
    return os.open(selected, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW | _CLOEXEC)


def _seal(fd: int, home: Path, custody, entries: Iterable, metadata: bytes) -> _Sink:
    backup_id = secrets.token_bytes(16)
    sink = _Sink(fd, custody, _encode_header(backup_id, metadata), backup_id)
    home_fd = _open_home(home, custody)
    try:
        with tarfile.open(fileobj=sink, mode="w|", format=tarfile.PAX_FORMAT) as tar:
            for source_path, relative_path, attributes in entries:
                _add_entry(tar, home_fd, _relative(home, source_path, relative_path), attributes)
    finally:
        os.close(home_fd)
    sink.finish()
    return sink


def _seal_stage(fd: int, parent: int, stage: str, home: Path, custody, entries, metadata: bytes) -> dict:
    try:
        written = os.fstat(fd)
        sink = _seal(fd, home, custody, entries, metadata)
        os.fsync(fd)
    finally:
        os.close(fd)
    # Reopen and authenticate exactly what reached the stage before anything is published.
    with _open_backup(stage, dir_fd=parent, owner_only=True) as handle:
        reopened = os.fstat(handle.fileno())
        if (reopened.st_dev, reopened.st_ino) != (written.st_dev, written.st_ino):
            raise MigrationArchiveError("The migration backup stage was replaced; nothing was published")
        _header, size, digest = _authenticate(handle, custody)
    if (size, digest) != (sink.size, sink.digest.hexdigest()):
        raise MigrationArchiveError("The sealed migration backup did not verify; nothing was published")
    return {"bytes": size, "sha256": digest}


@contextlib.contextmanager
def _destination_parent(directory: Path) -> Iterator[int]:
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW | _CLOEXEC)
    except OSError as exc:
        raise MigrationArchiveError("The migration backup directory must be an existing real directory") from exc
    try:
        if os.fstat(fd).st_uid != os.getuid():
            raise PermissionError("The migration backup directory must be owned by this user")
        yield fd
    finally:
        os.close(fd)


def _existing() -> MigrationArchiveError:
    return MigrationArchiveError("A migration backup already exists at the destination; it was retained")


def _publish(parent: int, stage: str, name: str) -> None:
    try:
        os.link(stage, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
    except FileExistsError as exc:
        raise _existing() from exc
    os.unlink(stage, dir_fd=parent)
    try:
        os.fsync(parent)
    except OSError as exc:
        raise MigrationArchiveError("The encrypted migration backup was published but its durability could "
                                    "not be confirmed; it was retained") from exc


def write_backup(home: Path, destination: Path, *, custody, entries: Iterable, metadata: bytes) -> dict:
    """Stream ``entries`` into a new encrypted rollback backup at ``destination``; never replace one.

    ``entries`` yields ``(source_path, relative_path, attributes)``; optional text attributes ``kind``,
    ``sha256`` and ``sqlite_fingerprint`` become ``HERMES.*`` PAX headers, and ``sha256`` is verified
    against the streamed source. ``metadata`` is the exact public ``.hermes-vault`` bytes. Returns
    ``{"bytes", "sha256"}`` of the whole ciphertext after durable publication. Failures before
    publication leave sources, any existing destination, and no stage behind.
    """
    home, destination = Path(home), Path(destination)
    metadata = _check_metadata(metadata)
    if destination.name in ("", ".", ".."):
        raise ValueError("A migration backup destination must name a new file")
    with _destination_parent(destination.parent) as parent:
        try:
            os.stat(destination.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise _existing()
        stage = ".migration-backup-stage-" + secrets.token_hex(16)
        fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC, 0o600, dir_fd=parent)
        try:
            summary = _seal_stage(fd, parent, stage, home, custody, entries, metadata)
            custody.check()
            _publish(parent, stage, destination.name)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(stage, dir_fd=parent)
    return summary


# --- Reading -----------------------------------------------------------------------------------

@contextlib.contextmanager
def _open_backup(path, *, dir_fd: int | None = None, owner_only: bool) -> Iterator[BinaryIO]:
    try:
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW | _CLOEXEC | _NONBLOCK, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise MigrationArchiveError("A migration backup must not be a symbolic link") from exc
        raise
    with os.fdopen(fd, "rb", buffering=_CHUNK) as handle:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise MigrationArchiveError("A migration backup must be a regular file")
        if owner_only and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise PermissionError("A migration backup must be an owner-only file")
        yield handle


class _Ciphertext:
    """Exact sequential reads from one pinned descriptor, hashing every consumed byte."""

    def __init__(self, handle: BinaryIO) -> None:
        self.handle, self.digest, self.size = handle, hashlib.sha256(), 0

    def exact(self, size: int) -> bytes:
        data = self.handle.read(size)
        if len(data) != size:
            raise _corrupt("is incomplete")
        self.digest.update(data)
        self.size += size
        return data

    def require_eof(self) -> None:
        if self.handle.read(1):
            raise _corrupt("has trailing data")

    def header(self) -> tuple[bytes, bytes, bytes]:
        """``(raw header, backup id, metadata)``; bounded, public, and not authenticated here."""
        if self.exact(len(_MAGIC)) != _MAGIC:
            raise MigrationArchiveError("Not a Hermes encrypted migration backup")
        (size,) = _HEADER_LENGTH.unpack(self.exact(_HEADER_LENGTH.size))
        if not 0 < size <= _MAX_HEADER:
            raise _corrupt()
        raw = self.exact(size)
        return (raw, *_decode_header(raw))


class _Records:
    """Strict ``H D* E EOF`` grammar over authenticated, bounded, sequenced frames."""

    def __init__(self, source: _Ciphertext, custody, header: bytes, backup_id: bytes) -> None:
        self.source, self.custody, self.backup_id = source, custody, backup_id
        self.purpose = _purpose(backup_id, header)
        self.sequence, self.total, self.plain, self.ended = 0, 0, hashlib.sha256(), False
        kind, body = self._next()
        if kind != b"H" or body != hashlib.sha256(header).digest():
            raise _corrupt()

    def _next(self) -> tuple[bytes, bytes]:
        prefix = self.source.exact(frames._LENGTH.size)
        (size,) = frames._LENGTH.unpack(prefix)
        if not _MIN_FRAME <= size <= _MAX_FRAME:
            raise _corrupt()
        try:
            payload, _end = frames.decode_frame_at(prefix + self.source.exact(size), 0, vault=self.custody,
                                                   purpose=self.purpose, strict=True)
        except VaultIntegrityError as exc:
            raise _corrupt("failed authentication") from exc
        kind, backup_id, sequence = _RECORD.unpack_from(payload)
        if backup_id != self.backup_id or sequence != self.sequence:
            raise _corrupt("records are out of order")
        self.sequence += 1
        return kind, payload[_RECORD.size:]

    def data(self) -> bytes | None:
        """The next authenticated tar chunk, or None after the verified end record and EOF."""
        if self.ended:
            return None
        kind, body = self._next()
        if kind == b"D":
            self.total += len(body)
            self.plain.update(body)
            return body
        if kind != b"E" or body != _END.pack(self.total, self.plain.digest()):
            raise _corrupt("failed its content verification")
        self.source.require_eof()
        self.ended = True
        return None


def _authenticate(handle: BinaryIO, custody) -> tuple[bytes, int, str]:
    """Verify the whole file; return ``(header, ciphertext bytes, ciphertext sha256)``."""
    source = _Ciphertext(handle)
    header, backup_id, _metadata = source.header()
    records = _Records(source, custody, header, backup_id)
    while records.data() is not None:
        pass
    return header, source.size, source.digest.hexdigest()


class _Plaintext:
    """Read-only tar source over authenticated chunks; plaintext only ever lives in memory."""

    def __init__(self, records: _Records) -> None:
        self.records, self.chunk, self.offset = records, b"", 0

    def read(self, size: int = -1) -> bytes:
        while self.offset >= len(self.chunk):
            chunk = self.records.data()
            if chunk is None:
                return b""
            self.chunk, self.offset = chunk, 0
        end = len(self.chunk) if size is None or size < 0 else self.offset + size
        data = self.chunk[self.offset:end]
        self.offset += len(data)
        return data


def _identity(info: os.stat_result) -> tuple:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _require_unchanged(path: Path, handle: BinaryIO, pinned: os.stat_result, size: int) -> None:
    try:
        named = os.stat(path, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise _changed() from exc
    if (_identity(os.fstat(handle.fileno())) != _identity(pinned) or pinned.st_size != size
            or (named.st_dev, named.st_ino) != (pinned.st_dev, pinned.st_ino)):
        raise _changed()


def read_backup_metadata(path: Path) -> bytes:
    """Return the public vault metadata from a bounded header parse. This is NOT authentication."""
    with _open_backup(Path(path), owner_only=False) as handle:
        return _Ciphertext(handle).header()[2]


@contextlib.contextmanager
def verified_tar(path: Path, *, custody, expected_sha256: str | None = None,
                 expected_metadata: bytes | None = None) -> Iterator[tarfile.TarFile]:
    """Yield a sequential ``r|`` TarFile only after the complete backup authenticated.

    Every frame, the header digest, sequence, end record and EOF are verified first. Members are then
    replayed from the same pinned descriptor; its identity, size and whole-ciphertext digest must
    match the verified pass. Members are read in memory via ``extractfile``; nothing is written.
    """
    path = Path(path)
    with _open_backup(path, owner_only=True) as handle:
        pinned = os.fstat(handle.fileno())
        header, size, digest = _authenticate(handle, custody)
        if expected_sha256 is not None and digest != expected_sha256:
            raise _changed()
        if expected_metadata is not None and _decode_header(header)[1] != expected_metadata:
            raise _changed()
        _require_unchanged(path, handle, pinned, size)
        handle.seek(0)
        source = _Ciphertext(handle)
        replayed, backup_id, _metadata = source.header()
        if replayed != header:
            raise _changed()
        records = _Records(source, custody, header, backup_id)
        with tarfile.open(fileobj=_Plaintext(records), mode="r|") as tar:
            yield tar
        while records.data() is not None:
            pass
        if (source.size, source.digest.hexdigest()) != (size, digest):
            raise _changed()
        _require_unchanged(path, handle, pinned, size)
