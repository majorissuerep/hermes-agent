"""Finish an authenticated outgoing checkpoint without discarding unconfirmed survivors."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
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
from tools import browser_outgoing_inventory as native_inventory
from tools import browser_outgoing_tar_witness as tar_witness

_CHUNK = 1024 * 1024


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


def _remaining(lease, document, ram, *, inventory=None, path=None):
    raw, digest, maximum = transfer.download_raw(lease, ram, path=path, stale=True)
    if inventory is not None:
        expected = native_inventory.original(lease, document["inventory"], inventory, ram)
        current, native_hash, native_size = native_inventory.receive(
            lease, ram, "native-current.json", path=path, stale=True)
        try:
            native_inventory.require_subset(current, expected)
            tar_witness.check(raw, current["entries"], custody=lease.custody,
                              binding=lease.binding if lease.bound and path is None else None)
        except (state.OutgoingProfileError, tarfile.TarError) as error:
            raise state.OutgoingProfileError(
                "Remaining browser state is not a proven unchanged native checkpoint subset "
                "(unknown/excluded/new/changed entries are retained). "
                f"Authenticated recovery: {lease.relative}; original target remainder: {path or lease.runtime.profile}. "
                "No bytes were deleted.") from error
        return digest, maximum, native_hash, native_inventory.effect_limit(lease, native_size)
    if digest == document["raw_hash"]:
        return digest, maximum
    raise state.OutgoingProfileError(
        "Older partial checkpoint has no complete original native inventory; no partial cleanup was attempted. "
        f"Authenticated recovery: {lease.relative}; original target remainder: {path or lease.runtime.profile}")


def _reference(lease, pinned):
    pinned.handle.seek(0)
    raw = pinned.handle.read(state._BOUND + 1)
    return json.loads(lease.custody.decrypt(
        raw, purpose="state", relpath=state.relative(lease.digest).as_posix()))


def _finish(lease, document, saved, *, stale, path, original, inventory):
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
            if inventory is not None:
                inventory.check()
            transfer.retire(lease, *original, path=path, stale=stale)
        elif not transfer.inspect(lease, path=path or lease.runtime.profile, stale=stale):
            _resume(lease, document, saved, reference, inventory=inventory, stale=stale, path=path)
        saved.check()
        reference.check()
        if inventory is not None:
            inventory.check()
        state.remove(lease.home, lease.digest, lease.custody, lease.token, expected=reference.identity)
        if inventory is not None:
            native_inventory.discard_completed(lease, inventory)


def _resume(lease, document, saved, reference, *, inventory, stale, path):
    from tools.browser_outgoing_profile import workspace

    if path is not None and inventory is None:
        saved.check()
        reference.check()
        transfer.retire(lease, document["raw_hash"], document["maximum"], path=path, stale=stale)
        return
    with workspace(lease, "outgoing-browser-retirement") as ram:
        remaining = _remaining(lease, document, ram, inventory=inventory, path=path)
        saved.check()
        reference.check()
        if inventory is not None:
            inventory.check()
        transfer.retire(lease, *remaining, path=path, stale=stale)


def finish(lease, document, *, stale=False, path=None, saved=None, original=None, inventory=None):
    """Keep original archive/ref/proof pins through the only deletion and reference cleanup."""
    metadata = document.get("inventory")
    proof_context = (nullcontext(inventory) if inventory is not None or metadata is None
                     else native_inventory.opened(lease, metadata))
    with proof_context as proof:
        if proof is not None:
            native_inventory.validate_opened(lease, metadata, proof)
        if saved is not None:
            return _finish(lease, document, saved, stale=stale, path=path, original=original, inventory=proof)
        with pinned_archive(lease) as opened:
            return _finish(lease, document, opened, stale=stale, path=path, original=original, inventory=proof)
