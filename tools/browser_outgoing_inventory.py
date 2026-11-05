"""Original native-entry evidence for resumable outgoing checkpoint retirement.

Only hashes and native facts enter the immutable encrypted companion. Complete
private profile bytes remain in the normal checkpoint. A later request may remove
only exact surviving original entries, never infer permission from an exclusion name.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import stat

from hermes_platform import remote_profile
from hermes_security import artifacts, browser_profile_archive as archive, tree_archive
from hermes_security import browser_profile_transport as transport
from tools import browser_outgoing_state as state, browser_outgoing_transfer as transfer

MAX_BYTES = 64 * 1024**2
_PREFIX = "outgoing-proof-"
_SLOT = ":native-retirement:v1"
_LOG = logging.getLogger(__name__)


def _digest(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def _integers(values, count):
    return (isinstance(values, list) and len(values) == count
            and all(type(value) is int and 0 <= value < 2**128 for value in values))


def _record(relative, record):
    if not isinstance(record, dict):
        raise state.OutgoingProfileError("Native retirement entry is malformed; retained")
    kind = record.get("kind")
    fields = {"kind", "inode", "mode", "uid"}
    if kind in (stat.S_IFREG, stat.S_IFLNK):
        fields |= {"sha256", "mtime_ns", "ctime_ns"}
        if (not _digest(record.get("sha256"))
                or any(type(record.get(k)) is not int or abs(record[k]) >= 2**127
                       for k in ("mtime_ns", "ctime_ns"))):
            raise state.OutgoingProfileError("Native retirement fingerprint is malformed; retained")
    if kind == stat.S_IFREG:
        fields.add("size")
        if type(record.get("size")) is not int or not 0 <= record["size"] <= archive.MAX_PROFILE_BYTES:
            raise state.OutgoingProfileError("Native retirement file size is invalid; retained")
    if (kind not in (stat.S_IFDIR, stat.S_IFREG, stat.S_IFLNK) or set(record) != fields
            or not _integers(record.get("inode"), 2) or type(record.get("uid")) is not int
            or record["uid"] < 0 or type(record.get("mode")) is not int or not 0 <= record["mode"] <= 0o7777):
        raise state.OutgoingProfileError("Native retirement entry type or identity is invalid; retained")
    if kind == stat.S_IFLNK and ("/" in relative or not archive._excluded(relative, stat.S_IFLNK)):
        raise state.OutgoingProfileError("Unknown native profile symlink was retained")


def decode(raw):
    if not 0 < len(raw) <= MAX_BYTES:
        raise state.OutgoingProfileError("Native retirement inventory exceeds its bound; retained")
    document = json.loads(raw)
    if (not isinstance(document, dict) or set(document) != {"version", "root", "entries"}
            or type(document["version"]) is not int or document["version"] != 1
            or not _integers(document["root"], 2) or not isinstance(document["entries"], dict)
            or len(document["entries"]) > archive.MAX_ENTRIES):
        raise state.OutgoingProfileError("Native retirement inventory is malformed; retained")
    for relative, record in document["entries"].items():
        tree_archive._relative(relative, "outgoing native retirement")
        _record(relative, record)
        parts = relative.split("/")
        for index in range(1, len(parts)):
            parent = document["entries"].get("/".join(parts[:index]))
            if not isinstance(parent, dict) or parent.get("kind") != stat.S_IFDIR:
                raise state.OutgoingProfileError("Native retirement inventory has an unknown parent; retained")
    return document


def receive(lease, ram, name, *, path=None, stale=False):
    """Bound target allocation and host parsing before accepting machine-side metadata."""
    facts = transfer.capacity(lease)
    if facts.available_bytes is None or facts.available_bytes <= 16 * 1024**2:
        raise state.OutgoingProfileError("Target native inventory capacity is unavailable; retained")
    maximum = min(MAX_BYTES, (facts.available_bytes - 16 * 1024**2) // 8,
                  transport.receive_bound(ram))
    target = ram / name
    binding = lease.binding if lease.bound and path is None else None
    argv = remote_profile.command("inventory", path or lease.runtime.profile, maximum=maximum,
                                  stale=stale, binding=binding, native_limit=maximum)
    descriptor = os.open(target, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(descriptor, "w+b") as sink:
        transfer.client(lease, argv, output_sink=sink, maximum=maximum, keep_input_open=True)
        sink.flush()
        before = os.fstat(sink.fileno())
        transport.require_capacity(ram, before.st_size * 8)
        sink.seek(0)
        raw = sink.read(MAX_BYTES + 1)
        after = os.fstat(sink.fileno())
        named = os.stat(target, follow_symlinks=False)
        if (len(raw) != before.st_size or (before.st_dev, before.st_ino, before.st_size) !=
                (after.st_dev, after.st_ino, after.st_size) or
                (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)):
            raise state.OutgoingProfileError("Native inventory spool changed; every copy retained")
    size = len(raw)
    document = decode(raw)
    if any(record["uid"] != lease.identity["uid"] for record in document["entries"].values()):
        raise state.OutgoingProfileError("Native inventory belongs to another target account; retained")
    return document, hashlib.sha256(raw).hexdigest(), size


def effect_limit(lease, size):
    # Native accounting also bounds the names and indexes retained beside JSON.
    limit = min(MAX_BYTES, size * 2 + 4096)
    transfer.require_target_capacity(lease, limit * 8)
    return limit


def validate_reference(lease_digest, metadata):
    if not isinstance(metadata, dict) or set(metadata) != {"relative", "bytes", "entries", "sha256", "recovery"}:
        raise state.OutgoingProfileError("Original native retirement reference is malformed; retained")
    expected = r"browser-profile/outgoing-proof-" + lease_digest[:32] + r"-[0-9a-f]{16}\.hbpa"
    if (not isinstance(metadata["relative"], str) or re.fullmatch(expected, metadata["relative"]) is None
            or type(metadata["bytes"]) is not int or not 0 < metadata["bytes"] <= MAX_BYTES
            or type(metadata["entries"]) is not int or not 0 <= metadata["entries"] <= archive.MAX_ENTRIES
            or not _digest(metadata["sha256"]) or not isinstance(metadata["recovery"], dict)):
        raise state.OutgoingProfileError("Original native retirement identity is invalid; retained")


def _policy():
    return tree_archive.TreePolicy(label="outgoing native inventory", max_bytes=MAX_BYTES, max_entries=1)


def _replay(lease, pinned, target=None):
    pinned.check()
    pinned.handle.seek(0)
    summary = tree_archive.replay_opened(
        pinned.handle, custody=lease.custody, purpose=archive.archive_purpose(pinned.relative),
        policy=_policy(), slot=lease.slot + _SLOT, digest=lease.digest, target=target)
    pinned.check()
    return summary


@contextmanager
def published(lease, ram, document):
    """Publish immutable original evidence, then hold its exact opened cipher until retirement."""
    from tools.browser_outgoing_retirement import _pin

    stage = ram / "native-proof"
    stage.mkdir(mode=0o700)
    raw = tree_archive.canonical_json(document)
    transport.require_capacity(ram, len(raw) * 8)
    descriptor = os.open(stage / "inventory.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as sink:
        sink.write(raw)
    relative = Path(archive.ARCHIVE_ROOT, _PREFIX + lease.digest[:32] + "-" + secrets.token_hex(8) + ".hbpa")
    identity = {"kind": "outgoing-native-retirement", "parent": lease.digest, "lease": lease.token}
    written = archive.write_archive(lease.home, relative, stage, custody=lease.custody,
                                    slot=lease.slot + _SLOT, identity=identity, digest=lease.digest, overwrite=False)
    with _pin(lease, relative, tree_archive._wire_bound(_policy())) as pinned:
        if _replay(lease, pinned) != written or written.entries != 1 or written.bytes != len(raw):
            raise state.OutgoingProfileError("Original native inventory did not authenticate; every copy retained")
        metadata = {"relative": relative.as_posix(), "bytes": len(raw), "entries": len(document["entries"]),
                    "sha256": pinned.digest, "recovery": asdict(written)}
        validate_reference(lease.digest, metadata)
        yield metadata, pinned


def validate_opened(lease, metadata, pinned):
    validate_reference(lease.digest, metadata)
    if (Path(metadata["relative"]) != pinned.relative or pinned.digest != metadata["sha256"]
            or asdict(_replay(lease, pinned)) != metadata["recovery"]):
        raise state.OutgoingProfileError("Original native retirement ciphertext changed; every copy retained")


@contextmanager
def opened(lease, metadata):
    from tools.browser_outgoing_retirement import _pin

    validate_reference(lease.digest, metadata)
    with _pin(lease, metadata["relative"], tree_archive._wire_bound(_policy())) as pinned:
        validate_opened(lease, metadata, pinned)
        yield pinned


def original(lease, metadata, pinned, ram):
    stage = ram / "original-native-proof"
    stage.mkdir(mode=0o700)
    summary = _replay(lease, pinned, stage)
    if asdict(summary) != metadata["recovery"] or summary.entries != 1 or summary.bytes != metadata["bytes"]:
        raise state.OutgoingProfileError("Original native retirement summary changed; every copy retained")
    path = stage / "inventory.json"
    transport.require_capacity(ram, metadata["bytes"] * 8)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != metadata["bytes"]:
            raise state.OutgoingProfileError("Original native inventory file is invalid; retained")
        raw = source.read(MAX_BYTES + 1)
    document = decode(raw)
    if len(raw) != metadata["bytes"] or len(document["entries"]) != metadata["entries"]:
        raise state.OutgoingProfileError("Original native retirement evidence is incomplete; retained")
    return document


def require_subset(current, expected):
    if current["root"] != expected["root"]:
        raise state.OutgoingProfileError("Original native profile root was replaced; retained")
    for relative, record in current["entries"].items():
        if expected["entries"].get(relative) != record:
            raise state.OutgoingProfileError("New or changed native profile survivor was retained; no unverified cleanup")


def discard_completed(lease, pinned):
    """An optional cipher proof cannot turn completed safe retirement into lost profile data."""
    try:
        pinned.check()
        with artifacts._parent(lease.home, pinned.relative) as parent:
            pinned.check()
            current = os.stat(pinned.relative.name, dir_fd=parent, follow_symlinks=False)
            if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns) != pinned.native:
                raise state.OutgoingProfileError("Completed retirement proof was replaced")
            os.unlink(pinned.relative.name, dir_fd=parent)
            os.fsync(parent)
    except (OSError, RuntimeError, ValueError) as error:
        _LOG.warning("Optional outgoing retirement proof cleanup is uncertain; complete checkpoint preserved (%s)",
                     type(error).__name__)
