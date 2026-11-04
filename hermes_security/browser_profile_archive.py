"""Authenticated encrypted archives of Hermes-owned browser profiles.

A Chromium profile is live secret state (SQLite cookie/login databases, LevelDB local storage,
preferences). Chromium needs real files, so the active copy lives in a proven RAM-only workspace
(``hermes_cli.browser_profile_store``); this module persists that copy as one durable sealed stream
and restores it. It is a dedicated secret-state domain: archives are never admitted artifact paths,
and the only readers are the owned-profile restore, explicit repair verification, and vault scan.

Wire format: the ``hermes_security.frames`` stream format, with the key and associated data bound to
the archive's exact home-relative path. Each frame payload is one record
``type (1) | archive id (16) | sequence (8)`` + body:

- ``H`` header: format, profile slot, identity (with its keyed digest)
- ``D`` directory, ``F`` file start (path, size, mtime), ``C`` file bytes
- ``E`` end: entry count, plaintext bytes, tree digest — must be the final record

A random per-archive id and strict sequence numbers stop reordering, splicing between generations,
and truncation. A reader rejects anything else before any restored tree is published.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import stat
import struct
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterator

from hermes_security import frames
from hermes_security.errors import VaultError, VaultLockedError

ARCHIVE_ROOT = "browser-profile"
LEGACY_DEBUG_ROOT = "chrome-debug"
ARCHIVE_SUFFIX = ".hbpa"
DEBUG_SLOT = "chrome-debug"
REAL_SLOT_PREFIX = "real-profile:"
# Runtime ownership metadata kept beside the active RAM copy; never part of the persisted profile.
RUNTIME_BINDING_NAME = ".hermes-browser-binding.json"

# Persistence is not the auth-only initial real-profile snapshot: CacheStorage, service workers,
# unknown app data and temporary-named files can be the only offline copy. Drop only these known
# Chromium layout locations. Names at another depth, and unexpected entry types, are not caches.
_ROOT_RUNTIME_NAMES = frozenset({
    "SingletonLock", "SingletonSocket", "SingletonCookie", "DevToolsActivePort",
    "RunningChromeVersion", "launch-stderr.log", RUNTIME_BINDING_NAME,
})
_ROOT_REGENERABLE_DIRECTORIES = frozenset({
    "ShaderCache", "GrShaderCache", "Crashpad", "Crash Reports", "BrowserMetrics",
})
_PROFILE_CACHE_DIRECTORIES = frozenset({"Cache", "Code Cache", "GPUCache"})

# Bounds: the real-profile copy keeps auth/login state only (tens of MB); a dedicated debug profile
# also keeps site storage. Exceeding a bound fails the persist explicitly and deletes nothing.
MAX_PROFILE_BYTES = 2 * 1024 ** 3
MAX_ENTRIES = 250_000
_CHUNK = 1024 * 1024
_MAX_META = 16 * 1024
_FORMAT = 1
_RECORD = struct.Struct(">c16sQ")
_LENGTH = struct.Struct(">I")
_MIN_FRAME = 12 + 16
_MAX_FRAME = _CHUNK + _RECORD.size + _MIN_FRAME
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


class ArchiveError(VaultError):
    """Profile persistence or restore failed; the message identifies publication state."""


@dataclass(frozen=True)
class ArchiveSummary:
    entries: int
    bytes: int
    digest: str


def real_slot(browser: str) -> str:
    return REAL_SLOT_PREFIX + browser


def archive_purpose(relative) -> str:
    return "browser-profile-archive:v1:" + PurePosixPath(relative).as_posix()


def _canonical(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def identity_digest(custody, slot: str, identity: dict) -> str:
    """Keyed digest of the exact owner slot and source identity; names leak no account text."""
    key = custody.derive_key("browser-profile:v1:identity")
    return hmac.new(key, _canonical({"slot": slot, "identity": identity}), hashlib.sha256).hexdigest()


def archive_relative(slot: str, digest: str) -> PurePosixPath:
    """Durable location: one debug-profile archive; one archive per real-profile source identity."""
    if slot == DEBUG_SLOT:
        return PurePosixPath(ARCHIVE_ROOT, DEBUG_SLOT + ARCHIVE_SUFFIX)
    return PurePosixPath(ARCHIVE_ROOT, f"real-{digest[:32]}{ARCHIVE_SUFFIX}")


def capture_custody(home: Path):
    """The current request's original unlock lineage for this exact home (never a later unlock)."""
    from hermes_security.artifacts import _Custody

    return _Custody(Path(home))


def browser_state_kind(relative) -> str | None:
    """Classify a home-relative path inside the owned browser-profile roots.

    ``archive`` is a sealed profile, ``stage`` an interrupted publication (ciphertext), and
    ``legacy`` anything an older build left raw (plaintext Chromium state).
    """
    parts = PurePosixPath(Path(relative).as_posix()).parts
    if not parts or parts[0] not in (ARCHIVE_ROOT, LEGACY_DEBUG_ROOT):
        return None
    if parts[0] == ARCHIVE_ROOT and len(parts) == 2:
        if parts[1].endswith(ARCHIVE_SUFFIX):
            return "archive"
        if parts[1].startswith(".stage-"):
            return "stage"
    return "legacy"


def archive_paths(home: Path) -> list[Path]:
    root = Path(home) / ARCHIVE_ROOT
    try:
        entries = sorted(os.scandir(root), key=lambda entry: entry.name)
    except (FileNotFoundError, NotADirectoryError):
        return []
    return [root / e.name for e in entries if e.name.endswith(ARCHIVE_SUFFIX) and e.is_file(follow_symlinks=False)]


def legacy_profile_roots(home: Path) -> list[Path]:
    """Raw profile directories older builds kept on persistent storage, without opening them."""
    home = Path(home)
    found = []
    with contextlib.suppress(FileNotFoundError, NotADirectoryError):
        found += [home / ARCHIVE_ROOT / e.name for e in sorted(os.scandir(home / ARCHIVE_ROOT), key=lambda e: e.name)
                  if e.is_dir(follow_symlinks=False)]
    debug = home / LEGACY_DEBUG_ROOT
    if debug.is_dir() and not debug.is_symlink():
        found.append(debug)
    return found


# --- Tree walking and digests -----------------------------------------------------------------

def _excluded(relative: str, kind: int) -> bool:
    """Only known root runtime objects and structural Chromium cache directories are expendable."""
    parts = PurePosixPath(relative).parts
    if len(parts) == 1:
        name = parts[0]
        return (name in _ROOT_RUNTIME_NAMES
                or (kind == stat.S_IFDIR and name in _ROOT_REGENERABLE_DIRECTORIES)
                or (kind == stat.S_IFREG and name == "BrowserMetrics-spare.pma"))
    if len(parts) != 2 or kind != stat.S_IFDIR or parts[1] not in _PROFILE_CACHE_DIRECTORIES:
        return False
    profile = parts[0]
    standard = profile in {"Default", "Guest Profile", "System Profile"}
    numbered = profile.startswith("Profile ") and profile[8:].isascii() and profile[8:].isdigit()
    return standard or numbered


@contextlib.contextmanager
def _source_directory(root: Path | int, relative: str = ""):
    """Pin every source ancestor without following links or changing legacy modes."""
    flags = os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW | _CLOEXEC
    fd = os.dup(root) if isinstance(root, int) else os.open(root, flags)
    try:
        for part in PurePosixPath(relative).parts:
            if part in {"..", "/"}:
                raise ArchiveError("A browser profile entry left its source directory")
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _directory_entries(root: Path | int, relative: str) -> list[tuple[str, int]]:
    with _source_directory(root, relative) as parent:
        with os.scandir(parent) as listing:
            return sorted((entry.name, stat.S_IFMT(entry.stat(follow_symlinks=False).st_mode))
                          for entry in listing)


def iter_tree(root: Path | int) -> Iterator[tuple[str, bool]]:
    """``(relative path, is_dir)`` in deterministic order; directories precede their contents.

    Only declared cache locations and root live-instance objects are excluded. An unexpected link or
    special file refuses persistence so verified legacy repair cannot erase it.
    """
    pending = [""]
    while pending:
        relative = pending.pop()
        children = []
        for name, kind in _directory_entries(root, relative):
            child = f"{relative}/{name}" if relative else name
            if _excluded(child, kind):
                continue
            if kind == stat.S_IFDIR:
                children.append(child)
                yield child, True
            elif kind == stat.S_IFREG:
                yield child, False
            else:
                raise ArchiveError(f"Unsupported browser profile entry {child!r}; every existing copy was retained")
        pending.extend(reversed(children))


class _TreeDigest:
    def __init__(self) -> None:
        self._hash = hashlib.sha256(b"hermes-browser-profile-tree|v1")

    def directory(self, relative: str) -> None:
        self._hash.update(b"D" + os.fsencode(relative) + b"\0")

    def file(self, relative: str, size: int, content: bytes) -> None:
        self._hash.update(b"F" + os.fsencode(relative) + b"\0" + size.to_bytes(8, "big") + content)

    def hexdigest(self) -> str:
        return self._hash.hexdigest()


@contextlib.contextmanager
def _source_file(root: Path | int, relative: str):
    path = PurePosixPath(_relative(relative))
    with _source_directory(root, path.parent.as_posix()) as parent:
        fd = os.open(path.name, os.O_RDONLY | _NOFOLLOW | _CLOEXEC | getattr(os, "O_NONBLOCK", 0), dir_fd=parent)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ArchiveError("A profile entry changed type while it was persisted")
        yield handle, info
        if handle.read(1) or os.fstat(handle.fileno()).st_mtime_ns != info.st_mtime_ns:
            raise ArchiveError("The browser profile changed while it was persisted (a browser is still writing it)")


def _read_chunks(handle: BinaryIO, size: int) -> Iterator[bytes]:
    remaining = size
    while remaining:
        chunk = handle.read(min(_CHUNK, remaining))
        if not chunk:
            raise ArchiveError("The browser profile changed while it was persisted (a browser is still writing it)")
        remaining -= len(chunk)
        yield chunk


def tree_digest(root: Path) -> ArchiveSummary:
    """Digest of exactly the entries an archive of ``root`` would hold (byte-identity proof)."""
    with _source_directory(root) as pinned:
        return _tree_digest(pinned)


def _tree_digest(root: int) -> ArchiveSummary:
    digest, entries, total = _TreeDigest(), 0, 0
    for relative, is_dir in iter_tree(root):
        entries += 1
        if is_dir:
            digest.directory(relative)
            continue
        with _source_file(root, relative) as (handle, info):
            content = hashlib.sha256()
            for chunk in _read_chunks(handle, info.st_size):
                content.update(chunk)
        digest.file(relative, info.st_size, content.digest())
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


def _meta(document: dict) -> bytes:
    body = _canonical(document)
    if len(body) > _MAX_META:
        raise ArchiveError("A browser profile entry name is too long to persist")
    return body


def _stream_file(sink: _RecordSink, root: int, relative: str, digest: _TreeDigest, budget: int) -> int:
    with _source_file(root, relative) as (handle, info):
        if info.st_size > budget:
            raise ArchiveError(
                f"The browser profile exceeds the {MAX_PROFILE_BYTES // 1024 ** 3} GiB persistence bound; "
                "nothing was discarded. Clear large site data in that browser profile and retry.")
        sink.record(b"F", _meta({"path": relative, "size": info.st_size, "mtime_ns": info.st_mtime_ns}))
        content = hashlib.sha256()
        for chunk in _read_chunks(handle, info.st_size):
            content.update(chunk)
            sink.record(b"C", chunk)
    digest.file(relative, info.st_size, content.digest())
    return info.st_size


def _stream_tree(sink: _RecordSink, root: int, header: dict) -> ArchiveSummary:
    sink.record(b"H", _meta({"format": _FORMAT, **header}))
    digest, entries, total = _TreeDigest(), 0, 0
    for relative, is_dir in iter_tree(root):
        entries += 1
        if entries > MAX_ENTRIES:
            raise ArchiveError(f"The browser profile has more than {MAX_ENTRIES} entries; nothing was discarded")
        if is_dir:
            sink.record(b"D", _meta({"path": relative}))
            digest.directory(relative)
        else:
            total += _stream_file(sink, root, relative, digest, MAX_PROFILE_BYTES - total)
    summary = ArchiveSummary(entries, total, digest.hexdigest())
    sink.record(b"E", _meta({"entries": entries, "bytes": total, "digest": summary.digest}))
    return summary


def write_archive(home: Path, relative, source: Path, *, custody, slot: str, identity: dict,
                  digest: str) -> ArchiveSummary:
    """Stream ``source`` into an owner-only ciphertext stage, authenticate it, then publish atomically.

    Pre-publication failures leave the previous archive and source untouched.
    A post-publication fsync failure explicitly reports uncertain durability;
    the new authenticated archive and active source are retained.
    """
    from hermes_security.artifacts import _parent

    home, relative = Path(home), PurePosixPath(relative)
    purpose = archive_purpose(relative)
    keyed = _PurposeKey(custody, purpose)
    header = {"slot": slot, "identity": identity, "identity_digest": digest}
    with _parent(home, Path(relative), create=True) as parent:
        stage = ".stage-" + secrets.token_hex(16)
        fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC, 0o600, dir_fd=parent)
        try:
            try:
                with _source_directory(source) as pinned:
                    summary = _stream_tree(_RecordSink(fd, keyed, purpose), pinned, header)
                    before, after = os.fstat(pinned), os.stat(source, follow_symlinks=False)
                    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                        raise ArchiveError("The browser profile source directory was replaced; every copy was retained")
                os.fsync(fd)
            finally:
                os.close(fd)
            if _replay_from(parent, stage, keyed, purpose, _Replay(slot, digest, None)) != summary:
                raise ArchiveError("The sealed browser profile did not authenticate before publication")
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

def _wire_bound() -> int:
    frames_max = 2 * MAX_ENTRIES + MAX_PROFILE_BYTES // _CHUNK + 4
    return MAX_PROFILE_BYTES + frames_max * (_MAX_META + _RECORD.size + _MIN_FRAME + _LENGTH.size)


def _read_exact(handle: BinaryIO, size: int, *, eof_ok: bool = False) -> bytes | None:
    data = handle.read(size)
    if eof_ok and not data:
        return None
    if len(data) != size:
        raise ArchiveError("The browser profile archive is incomplete")
    return data


def _iter_records(handle: BinaryIO, keyed: _PurposeKey, purpose: str) -> Iterator[tuple[bytes, bytes]]:
    archive_id, sequence = None, 0
    while (prefix := _read_exact(handle, _LENGTH.size, eof_ok=True)) is not None:
        (size,) = _LENGTH.unpack(prefix)
        if not _MIN_FRAME <= size <= _MAX_FRAME + _MAX_META:
            raise ArchiveError("The browser profile archive is corrupt")
        decoded = frames.decode_frame_at(prefix + _read_exact(handle, size), 0, vault=keyed, purpose=purpose,
                                         strict=True)
        if decoded is None or len(decoded[0]) < _RECORD.size:
            raise ArchiveError("The browser profile archive is corrupt")
        kind, record_id, record_sequence = _RECORD.unpack_from(decoded[0])
        archive_id = record_id if archive_id is None else archive_id
        if record_id != archive_id or record_sequence != sequence:
            raise ArchiveError("The browser profile archive records are out of order")
        sequence += 1
        yield kind, decoded[0][_RECORD.size:]


def _relative(text: Any) -> str:
    if not isinstance(text, str) or not text or "\x00" in text or len(text) > 4096:
        raise ArchiveError("The browser profile archive names an invalid entry")
    if any(part in ("", ".", "..") for part in text.split("/")):
        raise ArchiveError("The browser profile archive names an invalid entry")
    return text


def _document(body: bytes) -> dict:
    try:
        document = json.loads(body)
    except ValueError as exc:
        raise ArchiveError("The browser profile archive is corrupt") from exc
    if not isinstance(document, dict):
        raise ArchiveError("The browser profile archive is corrupt")
    return document


class _Replay:
    """Validate the record grammar; with a target, materialize into that fresh directory."""

    def __init__(self, slot: str | None, digest: str | None, target: Path | None) -> None:
        self.slot, self.expected, self.target = slot, digest, target
        self.header: dict | None = None
        self.summary: ArchiveSummary | None = None
        self.tree, self.entries, self.total = _TreeDigest(), 0, 0
        self.pending: list | None = None  # [relative, remaining, fd, content hash, mtime_ns, size]

    def feed(self, kind: bytes, body: bytes) -> None:
        handler = {b"H": self._header, b"D": self._directory, b"F": self._file,
                   b"C": self._chunk, b"E": self._end}.get(kind)
        if handler is None or self.summary is not None:
            raise ArchiveError("The browser profile archive is corrupt")
        handler(body)

    def _require_entries(self) -> None:
        if self.header is None or self.pending is not None:
            raise ArchiveError("The browser profile archive is corrupt")

    def _header(self, body: bytes) -> None:
        if self.header is not None:
            raise ArchiveError("The browser profile archive is corrupt")
        header = _document(body)
        if header.get("format") != _FORMAT:
            raise ArchiveError("The browser profile archive uses an unsupported format")
        if self.slot is not None and (header.get("slot") != self.slot
                                      or header.get("identity_digest") != self.expected):
            raise ArchiveError("The browser profile archive belongs to a different profile or account")
        self.header = header

    def _count(self) -> None:
        self.entries += 1
        if self.entries > MAX_ENTRIES:
            raise ArchiveError("The browser profile archive exceeds its entry bound")

    def _directory(self, body: bytes) -> None:
        self._require_entries()
        relative = _relative(_document(body).get("path"))
        self._count()
        if self.target is not None:
            os.mkdir(self.target / relative, 0o700)
        self.tree.directory(relative)

    def _file(self, body: bytes) -> None:
        self._require_entries()
        document = _document(body)
        relative, size, mtime = _relative(document.get("path")), document.get("size"), document.get("mtime_ns")
        if not isinstance(size, int) or size < 0 or size > MAX_PROFILE_BYTES - self.total or not isinstance(mtime, int):
            raise ArchiveError("The browser profile archive is corrupt")
        self._count()
        fd = -1
        if self.target is not None:
            fd = os.open(self.target / relative, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC, 0o600)
        self.pending = [relative, size, fd, hashlib.sha256(), mtime, size]
        if size == 0:
            self._finish_file()

    def _chunk(self, body: bytes) -> None:
        if self.pending is None or not body or len(body) > self.pending[1]:
            raise ArchiveError("The browser profile archive is corrupt")
        if self.pending[2] >= 0:
            from hermes_security.artifacts import _write_all

            _write_all(self.pending[2], body)
        self.pending[3].update(body)
        self.pending[1] -= len(body)
        if self.pending[1] == 0:
            self._finish_file()

    def _finish_file(self) -> None:
        relative, _remaining, fd, content, mtime, size = self.pending
        self.pending = None
        if fd >= 0:
            try:
                if os.utime in os.supports_fd:
                    os.utime(fd, ns=(mtime, mtime))
            finally:
                os.close(fd)
        self.tree.file(relative, size, content.digest())
        self.total += size

    def _end(self, body: bytes) -> None:
        self._require_entries()
        document = _document(body)
        summary = ArchiveSummary(self.entries, self.total, self.tree.hexdigest())
        if (document.get("entries"), document.get("bytes"), document.get("digest")) != (
                summary.entries, summary.bytes, summary.digest):
            raise ArchiveError("The browser profile archive failed its content verification")
        self.summary = summary

    def close(self) -> None:
        if self.pending is not None and self.pending[2] >= 0:
            os.close(self.pending[2])
        self.pending = None


def _replay_from(parent: int, name: str, keyed: _PurposeKey, purpose: str, replay: _Replay) -> ArchiveSummary:
    fd = os.open(name, os.O_RDONLY | _NOFOLLOW | _CLOEXEC | getattr(os, "O_NONBLOCK", 0), dir_fd=parent)
    with os.fdopen(fd, "rb", buffering=_CHUNK) as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError("A browser profile archive must be an owner-only regular file")
        if info.st_size > _wire_bound():
            raise ArchiveError("The browser profile archive exceeds its supported bound")
        try:
            for kind, body in _iter_records(handle, keyed, purpose):
                replay.feed(kind, body)
        finally:
            replay.close()
    if replay.summary is None:
        raise ArchiveError("The browser profile archive is incomplete")
    return replay.summary


def replay_archive(home: Path, relative, *, custody, slot: str | None = None, digest: str | None = None,
                   target: Path | None = None) -> ArchiveSummary:
    """Authenticate one owned archive end to end; with ``target`` restore into that fresh directory.

    ``slot``/``digest`` bind the restore to the exact owner slot and source identity. The caller
    publishes ``target`` only after this returns; a corrupt or foreign archive raises.
    """
    from hermes_security.artifacts import _parent

    relative = PurePosixPath(relative)
    if browser_state_kind(relative) != "archive":
        raise PermissionError("Not an owned browser profile archive path")
    purpose = archive_purpose(relative)
    keyed = _PurposeKey(custody, purpose)
    with _parent(Path(home), Path(relative)) as parent:
        summary = _replay_from(parent, relative.name, keyed, purpose, _Replay(slot, digest, target))
    custody.check()
    return summary


def archive_ciphertext_size(home: Path, relative) -> int | None:
    try:
        return os.stat(Path(home) / Path(relative), follow_symlinks=False).st_size
    except FileNotFoundError:
        return None
