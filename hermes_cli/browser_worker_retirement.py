"""Resume only authenticated, fully checkpointed WORKER cleanup after interruption.

The existing encrypted pending locator retains the original home, RAM inodes, owners and exact
ciphertext identity. No caller-selected path enters this operation. Normal producers refuse an
active intent; cancellation leaves it discoverable through the same fixed worker repair.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import secrets
import stat

from hermes_cli import browser_worker_store as store
from hermes_security import artifacts, browser_worker_archive as archive, browser_worker_pending as pending
from hermes_security.errors import VaultError


class _DigestReader:
    def __init__(self, handle, custody):
        self.handle, self.custody = handle, custody
        self.digest = hashlib.sha256()

    def fileno(self):
        return self.handle.fileno()

    def read(self, size=-1):
        self.custody.check()
        data = self.handle.read(size)
        self.digest.update(data)
        return data


class _Snapshot:
    def __init__(self, handle, parent, name, custody):
        self.handle, self.parent, self.name, self.custody = handle, parent, name, custody
        self.identity = self._facts(os.fstat(handle.fileno()))
        self.digest = None

    @staticmethod
    def _facts(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode, info.st_uid

    def check(self):
        self.custody.check()
        if (self._facts(os.fstat(self.handle.fileno())) != self.identity
                or self._facts(os.stat(self.name, dir_fd=self.parent, follow_symlinks=False)) != self.identity):
            raise store.WorkerStateError('The verified worker checkpoint changed; every remaining RAM copy retained')


@contextmanager
def _authenticated_snapshot(home, digest, custody):
    relative = archive.archive_relative(digest)
    with artifacts._parent(Path(home), relative) as parent:
        descriptor = os.open(relative.name, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, 'rb') as handle:
            snapshot = _Snapshot(handle, parent, relative.name, custody)
            reader = _DigestReader(handle, custody)
            archive.authenticate_ciphertext(reader, relative, custody=custody)
            snapshot.digest = reader.digest.hexdigest()
            snapshot.check()
            yield snapshot


def begin(resource, call):
    """Record closed targets only after a full checkpoint; all source data remains until then."""
    pending.sync(resource, call)
    document = pending.read_optional(resource.home, resource.digest, custody=call)
    targets = []
    paths = [resource.path] if resource.tree == resource.path/'tree' else [resource.tree, resource.path]
    for path in paths:
        call.check()
        if path.resolve(strict=True) != path or not store._prove(path).proven:
            raise store.WorkerStateError('Worker retirement target is no longer its canonical RAM directory')
        targets.append({'path':str(path), 'inode':list(store._identity(path))})
    with _authenticated_snapshot(resource.home, resource.digest, call) as snapshot:
        intent = {'targets':targets, 'archive_sha256':snapshot.digest}
    # Frozen v1 clients reject v2, preventing an older resident writer from re-sealing partial RAM.
    document = {**document, 'version':2, 'retirement':intent}
    pending._validate_retirement(document)
    pending.write(resource.home, pending.relative(resource.digest), document, custody=call)


def _remove_contents(descriptor, custody):
    for name in os.listdir(descriptor):
        custody.check()
        info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if info.st_uid != os.geteuid():
            raise store.WorkerStateError('Worker retirement found a foreign file; reference retained')
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC, dir_fd=descriptor)
            try:
                current = os.fstat(child)
                if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                    raise store.WorkerStateError('Worker retirement directory changed; reference retained')
                _remove_contents(child, custody)
                custody.check()
                after = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino):
                    raise store.WorkerStateError('Worker retirement directory was replaced; reference retained')
                os.rmdir(name, dir_fd=descriptor)
            finally:
                os.close(child)
        elif stat.S_ISREG(info.st_mode):
            custody.check()
            os.unlink(name, dir_fd=descriptor)
        else:
            raise store.WorkerStateError('Worker retirement found an unexpected link or special file; reference retained')


def _remove_target(target, custody):
    from hermes_platform.owned_cleanup import capture_directories, close_directories

    path = Path(target['path'])
    try:
        path.lstat()
    except FileNotFoundError:
        return
    if not store._prove(path).proven:
        raise store.WorkerStateError('Worker retirement target is not proven RAM')
    records = capture_directories([path])
    try:
        record = records[0]
        if [record['device'], record['inode']] != target['inode']:
            raise store.WorkerStateError('Worker retirement target was replaced; every copy retained')
        custody.check()
        child = os.open(record['name'], os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC, dir_fd=record['parent'])
        try:
            info = os.fstat(child)
            if [info.st_dev, info.st_ino] != target['inode']:
                raise store.WorkerStateError('Worker retirement target changed before deletion')
            _remove_contents(child, custody)
            custody.check()
            current = os.stat(record['name'], dir_fd=record['parent'], follow_symlinks=False)
            if [current.st_dev, current.st_ino] != target['inode']:
                raise store.WorkerStateError('Worker retirement target changed before completion')
            os.rmdir(record['name'], dir_fd=record['parent'])
        finally:
            os.close(child)
    finally:
        close_directories(records)


def finish(home, digest, custody):
    from hermes_platform.processes import process_record_is_dead
    from hermes_platform.runtime_health import current_instantiation_epoch

    document = pending.read_optional(home, digest, custody=custody)
    if document is None or document.get('retirement') is None:
        raise store.WorkerStateError('The authenticated worker retirement intent changed; no paths removed')
    pending._validate_retirement(document)
    targets = document['retirement']['targets']
    epoch = document['epoch'] == current_instantiation_epoch()
    if not epoch and any(os.path.lexists(target['path']) for target in targets):
        raise store.WorkerStateError('Worker retirement belongs to another machine incarnation; existing paths retained')
    if epoch and not all(process_record_is_dead(item) for item in [*document['owners'], *document['processes']]):
        raise store.WorkerStateError('Worker retirement owner is live or unknown; every copy retained')
    with _authenticated_snapshot(home, digest, custody) as snapshot:
        if snapshot.digest != document['retirement']['archive_sha256']:
            raise store.WorkerStateError('Worker checkpoint changed after retirement admission; every copy retained')
        for target in targets:
            snapshot.check()
            _remove_target(target, snapshot)
        snapshot.check()
        note = Path(archive.ARCHIVE_ROOT, '.recovered-'+digest+'-'+secrets.token_hex(8)+'.json')
        pending.write(home, note, {**document, 'outcome':'Verified worker retirement completed; original checkpoint unchanged'},
                      custody=custody, overwrite=False)
        snapshot.check()
        pending.remove(home, digest, custody=custody)


def resume(home, custody):
    saved, retained = [], []
    for reference in pending.paths(home):
        document = pending.read(home, reference.relative_to(home), custody=custody)
        if document.get('retirement') is None:
            continue
        try:
            selected = store.WorkerResource(home, document['identity'], custody, base=Path(document['path']).parent)
            if selected.path != Path(document['path']):
                raise store.WorkerStateError('Worker retirement resource path changed')
            with store._lock(selected.lock_path, check_alive=custody.check):
                current = pending.read_optional(home, document['digest'], custody=custody)
                if current is None or current.get('retirement') is None:
                    continue  # another admitted repair completed before this lock was acquired
                finish(home, document['digest'], custody)
            saved.append(archive.archive_relative(document['digest']).as_posix())
        except (VaultError, OSError, ValueError) as exc:
            retained.append(f'{reference}: {exc}; verified checkpoint and pending RAM locations retained. '
                            'Run browser.worker.repair after closing its original owners.')
    return saved, retained
