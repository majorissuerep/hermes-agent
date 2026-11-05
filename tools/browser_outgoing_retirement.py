"""Finish an authenticated outgoing checkpoint without discarding unconfirmed survivors."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import stat
import tarfile

from hermes_security import artifacts, browser_profile_archive as archive
from hermes_security import browser_profile_transport as transport, tree_archive
from tools import browser_outgoing_state as state, browser_outgoing_transfer as transfer

_CHUNK = 1024 * 1024
_META_BUDGET = 64 * 1024
_BINDING = archive.RUNTIME_BINDING_NAME


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


class _PinnedCipher:
    def __init__(self, home, relative, custody, parent, handle, maximum):
        self.home, self.relative, self.custody = home, relative, custody
        self.parent, self.handle, self.maximum = parent, handle, maximum
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > maximum):
            raise state.OutgoingProfileError("Checkpoint ciphertext is not an owned bounded regular file; retained")
        self.native = _stamp(info)
        parent_info = os.fstat(parent)
        self.parent_identity = parent_info.st_dev, parent_info.st_ino
        self.digest = self._hash()
        self.check()

    @property
    def identity(self):
        return (*self.native, self.digest)

    def _hash(self):
        self.handle.seek(0)
        digest, total = hashlib.sha256(), 0
        while chunk := self.handle.read(_CHUNK):
            self.custody.check()
            total += len(chunk)
            if total > self.maximum:
                raise state.OutgoingProfileError("Checkpoint ciphertext exceeds its bound; retained")
            digest.update(chunk)
        self.handle.seek(0)
        return digest.hexdigest()

    def _check_native(self):
        with artifacts._parent(self.home, self.relative) as parent:
            current_parent = os.fstat(parent)
            current = os.stat(self.relative.name, dir_fd=parent, follow_symlinks=False)
            if ((current_parent.st_dev, current_parent.st_ino) != self.parent_identity
                    or _stamp(current) != self.native or _stamp(os.fstat(self.handle.fileno())) != self.native):
                raise state.OutgoingProfileError("Original checkpoint ciphertext was replaced; every copy retained")

    def check(self):
        self.custody.check()
        self._check_native()
        if self._hash() != self.digest or _stamp(os.fstat(self.handle.fileno())) != self.native:
            raise state.OutgoingProfileError("Original checkpoint ciphertext changed; every copy retained")
        # Hashing can take time: revalidate the named copy after the complete read too.
        self._check_native()

    def replay(self, lease, *, target=None):
        self.handle.seek(0)
        result = archive.replay_opened(self.handle, lease.relative, custody=lease.custody,
                                       slot=lease.slot, digest=lease.digest, target=target)
        self.check()
        return result


@contextmanager
def _pin(lease, relative, maximum):
    relative = Path(relative)
    with artifacts._parent(lease.home, relative) as parent:
        descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                             dir_fd=parent)
        with os.fdopen(descriptor, "rb") as handle:
            yield _PinnedCipher(lease.home, relative, lease.custody, parent, handle, maximum)


@contextmanager
def pinned_archive(lease):
    with _pin(lease, lease.relative, tree_archive._wire_bound(archive._policy())) as pinned:
        yield pinned


class _TarReader:
    """Bound stdlib tar metadata before parsing; regular bodies are streamed separately."""
    def __init__(self, source):
        self.source, self.metadata_left, self.data = source, _META_BUDGET, False

    def read(self, size=-1):
        if size < 0 or size > _CHUNK or (not self.data and size > self.metadata_left):
            raise state.OutgoingProfileError("Remaining profile tar metadata exceeds its bound")
        if not self.data:
            self.metadata_left -= size
        return self.source.read(size)

    def seek(self, offset, whence=0):
        return self.source.seek(offset, whence)

    def tell(self):
        return self.source.tell()


def _file_hash(handle, custody):
    digest = hashlib.sha256()
    while block := handle.read(_CHUNK):
        custody.check()
        digest.update(block)
    return digest.digest()


def _same_entry(incoming, member, relative, original, reader, custody):
    if member.isdir():
        with tree_archive._source_directory(original, "checkpoint survivor", relative) as descriptor:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise state.OutgoingProfileError("Remaining profile entry changed type")
        return
    if not member.isreg() or member.sparse is not None or member.size < 0:
        raise state.OutgoingProfileError("Remaining profile has an unsupported entry type")
    with tree_archive._source_file(original, "checkpoint survivor", relative) as (expected, info):
        if info.st_size != member.size:
            raise state.OutgoingProfileError("Remaining profile file changed size")
        reader.data = True
        try:
            with incoming.extractfile(member) as current:
                if _file_hash(current, custody) != _file_hash(expected, custody):
                    raise state.OutgoingProfileError("Remaining profile file changed content")
        finally:
            reader.data = False


def _binding(incoming, member, lease, reader):
    if not member.isreg() or member.sparse is not None or not 0 < member.size <= 16384:
        raise state.OutgoingProfileError("Original remaining profile binding is invalid")
    reader.data = True
    try:
        with incoming.extractfile(member) as source:
            raw = source.read(16385)
    finally:
        reader.data = False
    if len(raw) != member.size or json.loads(raw) != lease.binding:
        raise state.OutgoingProfileError("Original remaining profile binding was replaced")


def _tar_end(source, offset):
    size = os.fstat(source.fileno()).st_size
    if size - offset < 1024 or (size - offset) % 512:
        raise state.OutgoingProfileError("Remaining profile tar has no complete terminator")
    source.seek(offset)
    while block := source.read(_CHUNK):
        if any(block):
            raise state.OutgoingProfileError("Remaining profile tar has unconfirmed trailing data")


def _subset(raw, original, lease):
    policy, previous, binding, root, count = archive._policy(), None, False, False, 0
    with raw.open("rb") as source:
        reader = _TarReader(source)
        with tarfile.open(fileobj=reader, mode="r:") as incoming:
            while True:
                reader.metadata_left = _META_BUDGET
                member = incoming.next()
                if member is None:
                    break
                lease.custody.check()
                count += 1
                if count > policy.max_entries + 2:
                    raise state.OutgoingProfileError("Remaining profile exceeds its entry bound")
                relative = transport._relative(member)
                if relative is None:
                    if root:
                        raise state.OutgoingProfileError("Remaining profile repeats its root entry")
                    root = True
                else:
                    key = tuple(relative.split("/"))
                    if previous is not None and key <= previous:
                        raise state.OutgoingProfileError("Remaining profile repeats or reorders an entry")
                    previous = key
                    if relative == _BINDING:
                        _binding(incoming, member, lease, reader)
                        binding = True
                    else:
                        _same_entry(incoming, member, relative, original, reader, lease.custody)
                incoming.members.clear()  # No links are admitted; only this member is needed.
            _tar_end(source, incoming.offset)
    if not binding:
        raise state.OutgoingProfileError("Original remaining profile binding is missing")


def _remaining(lease, document, saved, summary, ram):
    raw, digest, maximum = transfer.download_raw(lease, ram, stale=True)
    if digest == document["raw_hash"]:
        return digest, maximum  # Exact original raw source also covers its declared exclusions.
    if document.get("archive_cipher_sha256") is None:
        raise state.OutgoingProfileError(
            "Older partial checkpoint has no original ciphertext identity; no partial cleanup was attempted. "
            f"Authenticated recovery: {lease.relative}; original target remainder: {lease.runtime.profile}")
    transport.require_capacity(ram, summary.bytes + summary.entries * 8192 + _META_BUDGET)
    restored = ram / "authenticated-checkpoint"
    restored.mkdir(mode=0o700)
    if saved.replay(lease, target=restored) != summary:
        raise state.OutgoingProfileError("Original authenticated checkpoint changed")
    try:
        _subset(raw, restored, lease)
    except (OSError, ValueError, tarfile.TarError, state.OutgoingProfileError) as error:
        raise state.OutgoingProfileError(
            "Remaining browser state is not a proven unchanged checkpoint subset "
            f"(unknown/excluded/new/changed entries are retained). Authenticated recovery: {lease.relative}; "
            f"original target remainder: {lease.runtime.profile}. No bytes were deleted.") from error
    return digest, maximum


def _reference(lease, pinned):
    pinned.handle.seek(0)
    raw = pinned.handle.read(state._BOUND + 1)
    return json.loads(lease.custody.vault.decrypt(
        raw, purpose="state", relpath=state.relative(lease.digest).as_posix(),
        authority=lease.custody.authority))


def _finish(lease, document, saved, *, stale, path, original):
    if document.get("archive_cipher_sha256", saved.digest) != saved.digest:
        raise state.OutgoingProfileError("Original checkpoint ciphertext identity changed; every copy retained")
    summary = saved.replay(lease)
    if asdict(summary) != document.get("recovery"):
        raise state.OutgoingProfileError("Saved checkpoint changed; archive and target retained")
    with _pin(lease, state.relative(lease.digest), state._BOUND) as reference:
        if _reference(lease, reference) != document:
            raise state.OutgoingProfileError("Outgoing checkpoint reference changed; retained")
        if original is not None:
            saved.check()
            reference.check()
            transfer.retire(lease, *original, path=path, stale=stale)
        elif not transfer.inspect(lease, path=path or lease.runtime.profile, stale=stale):
            _resume(lease, document, saved, reference, summary, stale=stale, path=path)
        saved.check()
        reference.check()
        state.remove(lease.home, lease.digest, lease.custody, lease.token, expected=reference.identity)


def _resume(lease, document, saved, reference, summary, *, stale, path):
    from tools.browser_outgoing_profile import workspace

    if path is not None:
        saved.check()
        reference.check()
        transfer.retire(lease, document["raw_hash"], document["maximum"], path=path, stale=stale)
        return
    with workspace(lease, "outgoing-browser-retirement") as ram:
        digest, maximum = _remaining(lease, document, saved, summary, ram)
        saved.check()
        reference.check()
        transfer.retire(lease, digest, maximum, stale=stale)


def finish(lease, document, *, stale=False, path=None, saved=None, original=None):
    """Keep original archive/ref pins through the only deletion and final reference cleanup."""
    if saved is not None:
        return _finish(lease, document, saved, stale=stale, path=path, original=original)
    with pinned_archive(lease) as opened:
        return _finish(lease, document, opened, stale=stale, path=path, original=original)
