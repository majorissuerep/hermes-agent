"""Hash-only original control inventory in the existing encrypted WORKER archive domain."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import stat

from hermes_cli import browser_worker_store as store
from hermes_security import browser_worker_archive as archive, tree_archive
from hermes_security.protected_workspace import protected_workspace

MAX_BYTES = 64 * 1024**2
MAX_ENTRIES = archive.MAX_ENTRIES
HEADROOM = 64 * 1024**2


def capacity(base, needed):
    budget = store._capacity(base, proof=store._prove(base))
    if budget.available_bytes is None or budget.available_bytes < needed + HEADROOM:
        raise store.WorkerStateError('Retirement proof needs more proven RAM; original checkpoint and RAM retained')


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode, info.st_uid


def file_hash(parent, name, check):
    descriptor = os.open(name, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(descriptor, 'rb') as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid():
            raise store.WorkerStateError('Retirement survivor is not an owned regular file')
        digest = hashlib.sha256()
        while block := handle.read(1024*1024):
            check()
            digest.update(block)
        after = os.fstat(handle.fileno())
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if _stamp(before) != _stamp(after) or _stamp(before) != _stamp(named):
            raise store.WorkerStateError('Retirement survivor changed during its hash; every copy retained')
        check()
        return digest.hexdigest(), after


def control_record(parent, name, check):
    check()
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    kind = stat.S_IFMT(info.st_mode)
    if info.st_uid != os.geteuid() or kind not in {
            stat.S_IFDIR,stat.S_IFREG,stat.S_IFLNK,stat.S_IFSOCK,stat.S_IFIFO}:
        raise store.WorkerStateError('Unknown or foreign worker control entry; every copy retained')
    record = {'kind':kind, 'inode':[info.st_dev,info.st_ino], 'mode':stat.S_IMODE(info.st_mode), 'uid':info.st_uid}
    if kind == stat.S_IFREG:
        record['sha256'], after = file_hash(parent, name, check)
        if _stamp(info) != _stamp(after):
            raise store.WorkerStateError('Worker control changed before its hash; every copy retained')
        record.update(size=after.st_size, mtime_ns=after.st_mtime_ns)
    elif kind == stat.S_IFLNK:
        record['sha256'] = hashlib.sha256(os.fsencode(os.readlink(name, dir_fd=parent))).hexdigest()
    if _stamp(info) != _stamp(os.stat(name, dir_fd=parent, follow_symlinks=False)):
        raise store.WorkerStateError('Worker control changed during inventory; every copy retained')
    return record


def _capture(descriptor, prefix, entries, resource, check, used):
    for name in sorted(os.listdir(descriptor)):
        check()
        relative = prefix/name
        record = control_record(descriptor, name, check)
        encoded = tree_archive.canonical_json({relative.as_posix():record})
        used[0] += len(encoded)
        if used[0] > MAX_BYTES or len(entries) >= MAX_ENTRIES:
            raise store.WorkerStateError('Worker control inventory exceeds its bound; all state retained')
        capacity(resource.base, used[0]*8)
        entries[relative.as_posix()] = record
        if record['kind'] == stat.S_IFDIR and resource.path/relative != resource.tree:
            child = os.open(name, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC, dir_fd=descriptor)
            try:
                info = os.fstat(child)
                if [info.st_dev,info.st_ino] != record['inode']:
                    raise store.WorkerStateError('Worker control directory changed during inventory')
                _capture(child, relative, entries, resource, check, used)
            finally:
                os.close(child)


def create(resource, custody):
    """No raw control bytes, capability or lineage record is persisted in the companion."""
    entries = {}
    with tree_archive._source_directory(resource.path, 'worker control') as descriptor:
        _capture(descriptor, Path(), entries, resource, custody.check, [0])
    raw = tree_archive.canonical_json({'version':1, 'parent':resource.digest, 'entries':entries})
    if len(raw) > MAX_BYTES:
        raise store.WorkerStateError('Worker control inventory exceeds its bound; all state retained')
    capacity(resource.base, len(raw)*8)
    identity = {'kind':'worker-retirement-control', 'parent':resource.digest, 'nonce':secrets.token_hex(16)}
    digest = archive.identity_digest(custody, identity)
    relative = archive.archive_relative(digest)
    with protected_workspace('worker-retire-control', base=resource.base, prove=store._prove) as stage:
        descriptor = os.open(stage/'control.json', os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC, 0o600)
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(raw)
        archive.write_archive(resource.home, relative, stage, custody=custody, identity=identity, digest=digest)
    custody.check()
    return {'digest':digest, 'bytes':len(raw), 'entries':len(entries)}


def load(path, document):
    expected = document['retirement']['control']
    descriptor = os.open(path/'control.json', os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != expected['bytes'] or info.st_size > MAX_BYTES:
            raise store.WorkerStateError('Worker control proof has invalid size or type')
        decoded = json.load(handle)
    if (not isinstance(decoded, dict) or decoded.get('version') != 1
            or decoded.get('parent') != document['digest'] or not isinstance(decoded.get('entries'), dict)
            or len(decoded['entries']) != expected['entries']):
        raise store.WorkerStateError('Worker control proof has invalid original identity')
    return decoded['entries']
