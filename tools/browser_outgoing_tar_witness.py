"""Bounded raw transport hygiene after the complete native subset was proved.

This parser grants no deletion authority: the authenticated original inventory,
current native identities and per-entry effect guards remain mandatory. It checks
that the received tar really describes that current subset, without writing files.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tarfile

from hermes_security import browser_profile_archive as archive
from hermes_security import browser_profile_transport as transport
from tools.browser_outgoing_state import OutgoingProfileError

_CHUNK = 1024 * 1024
_META = 64 * 1024


class _Reader:
    def __init__(self, source):
        self.source, self.left, self.data = source, _META, False

    def read(self, size=-1):
        if size < 0 or size > _CHUNK or (not self.data and size > self.left):
            raise OutgoingProfileError("Remaining tar metadata exceeds its bound; retained")
        if not self.data:
            self.left -= size
        return self.source.read(size)

    def seek(self, offset, whence=0):
        return self.source.seek(offset, whence)

    def tell(self):
        return self.source.tell()


def _regular(incoming, member, expected, reader, custody, binding):
    if member.sparse is not None or member.size != expected["size"] or member.size < 0:
        raise OutgoingProfileError("Remaining tar file size/type changed; retained")
    if binding is not None and not 0 < member.size <= 16384:
        raise OutgoingProfileError("Remaining tar binding exceeds its bound; retained")
    digest, body = hashlib.sha256(), bytearray()
    reader.data = True
    try:
        with incoming.extractfile(member) as source:
            while block := source.read(_CHUNK):
                custody.check()
                digest.update(block)
                if binding is not None:
                    body.extend(block)
    finally:
        reader.data = False
    if digest.hexdigest() != expected["sha256"]:
        raise OutgoingProfileError("Remaining tar file bytes changed; retained")
    if binding is not None and json.loads(body) != binding:
        raise OutgoingProfileError("Remaining tar binding changed; retained")


def _entry(incoming, member, relative, expected, reader, custody, binding):
    kind = expected["kind"]
    if member.isdir() and kind == stat.S_IFDIR and member.size == 0:
        return
    if member.isreg() and kind == stat.S_IFREG:
        _regular(incoming, member, expected, reader, custody,
                 binding if relative == archive.RUNTIME_BINDING_NAME else None)
        return
    if (member.issym() and kind == stat.S_IFLNK and "/" not in relative
            and archive._excluded(relative, stat.S_IFLNK) and member.size == 0
            and hashlib.sha256(os.fsencode(member.linkname)).hexdigest() == expected["sha256"]):
        return
    raise OutgoingProfileError("Remaining tar has an unconfirmed link or special entry; retained")


def _end(source, offset):
    size = os.fstat(source.fileno()).st_size
    if size - offset < 1024 or (size - offset) % 512:
        raise OutgoingProfileError("Remaining tar has no complete terminator; retained")
    source.seek(offset)
    while block := source.read(_CHUNK):
        if any(block):
            raise OutgoingProfileError("Remaining tar has unconfirmed trailing bytes; retained")


def check(path, records, *, custody, binding=None):
    """Check complete ordered transport against proved current native records; never extract it."""
    seen, previous, root, count = set(), None, False, 0
    with path.open("rb") as source:
        reader = _Reader(source)
        with tarfile.open(fileobj=reader, mode="r:") as incoming:
            while True:
                reader.left = _META
                member = incoming.next()
                if member is None:
                    break
                custody.check()
                count += 1
                if count > archive.MAX_ENTRIES + 2:
                    raise OutgoingProfileError("Remaining tar exceeds its entry bound; retained")
                relative = transport._relative(member)
                if relative is None:
                    if root:
                        raise OutgoingProfileError("Remaining tar repeats its root; retained")
                    root = True
                else:
                    key = tuple(relative.split("/"))
                    if previous is not None and key <= previous:
                        raise OutgoingProfileError("Remaining tar repeats or reorders a path; retained")
                    previous = key
                    if relative not in records:
                        raise OutgoingProfileError("Remaining tar contains an unknown entry; retained")
                    _entry(incoming, member, relative, records[relative], reader, custody, binding)
                    seen.add(relative)
                incoming.members.clear()
            _end(source, incoming.offset)
    if not root or seen != set(records):
        raise OutgoingProfileError("Remaining tar omits a native entry; retained")
