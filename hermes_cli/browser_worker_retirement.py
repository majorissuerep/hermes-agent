"""Resume only authenticated, fully checkpointed WORKER cleanup after interruption.

The existing encrypted pending locator retains the original home, RAM inodes, owners and exact
ciphertext identity. No caller-selected path enters this operation. Normal producers refuse an
active intent; cancellation leaves it discoverable through the same fixed worker repair.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack, nullcontext
import hashlib
import os
from pathlib import Path
import secrets
import stat
import time

from hermes_cli import browser_worker_store as store
from hermes_cli import browser_worker_retirement_proof as proof
from hermes_cli.browser_worker_retirement_survivors import AbsentTargets, Survivors, target_present
from hermes_security.protected_workspace import protected_workspace
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
    def __init__(self, handle, parent, home, relative, custody):
        self.handle, self.home, self.relative, self.custody = handle, home, relative, custody
        parent_info = os.fstat(parent)
        self.parent_identity = parent_info.st_dev, parent_info.st_ino
        self.identity = self._facts(os.fstat(handle.fileno()))
        self.digest = None
        self.summary = None

    @staticmethod
    def _facts(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode, info.st_uid

    def check(self):
        self.custody.check()
        with artifacts._parent(self.home, self.relative) as parent:
            info = os.fstat(parent)
            if ((info.st_dev, info.st_ino) != self.parent_identity
                    or self._facts(os.fstat(self.handle.fileno())) != self.identity
                    or self._facts(os.stat(self.relative.name, dir_fd=parent, follow_symlinks=False)) != self.identity):
                raise store.WorkerStateError('The verified worker checkpoint path changed; every remaining RAM copy retained')


    def replay(self, target):
        self.check()
        self.handle.seek(0)
        reader = _DigestReader(self.handle, self.custody)
        summary = archive.replay_opened(reader, self.relative, custody=self.custody, target=target)
        if summary != self.summary or reader.digest.hexdigest() != self.digest:
            raise store.WorkerStateError('Worker checkpoint changed during proof replay; every copy retained')
        self.check()


@contextmanager
def _completion_lock(handle, custody):
    """Serialize reference completion on the immutable archive, even after its RAM base vanished."""
    from hermes_platform.processes import release_file_lock, try_acquire_file_lock

    store._wait_for_lock(lambda: try_acquire_file_lock(handle), time.monotonic()+60, custody.check)
    try:
        yield
    finally:
        release_file_lock(handle)


@contextmanager
def _authenticated_snapshot(home, digest, custody, *, locked=False):
    relative = archive.archive_relative(digest)
    with artifacts._parent(Path(home), relative) as parent:
        descriptor = os.open(relative.name, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, 'rb') as handle, (_completion_lock(handle, custody) if locked else nullcontext()):
            snapshot = _Snapshot(handle, parent, Path(home), Path(relative), custody)
            reader = _DigestReader(handle, custody)
            snapshot.summary = archive.authenticate_ciphertext(reader, relative, custody=custody)
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
        targets.append({'path':str(path), 'inode':list(store._identity(path)),
                        'parent_inode':list(store._identity(path.parent))})
    control = proof.create(resource, call)
    with _authenticated_snapshot(resource.home, resource.digest, call) as snapshot:
        with _authenticated_snapshot(resource.home, control['digest'], call) as companion:
            control['sha256'] = companion.digest
            intent = {'targets':targets, 'archive_sha256':snapshot.digest, 'control':control}
    # Published v1 and unreleased v2 clients refuse this closed proof-bearing intent.
    document = {**document, 'version':3, 'retirement':intent}
    pending._validate_retirement(document)
    pending.write(resource.home, pending.relative(resource.digest), document, custody=call)


def _remove_contents(descriptor, custody, target, prefix=Path()):
    for name in sorted(os.listdir(descriptor)):
        relative = prefix/name
        before = custody.entry(target, relative, descriptor, name)
        if stat.S_ISDIR(before[5]):
            child = os.open(name, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC, dir_fd=descriptor)
            try:
                if proof._stamp(os.fstat(child)) != before:
                    raise store.WorkerStateError('Worker retirement directory changed; reference retained')
                _remove_contents(child, custody, target, relative)
                after = custody.entry(target, relative, descriptor, name)
                if after[:2] != before[:2]:
                    raise store.WorkerStateError('Worker retirement directory was replaced; reference retained')
                os.rmdir(name, dir_fd=descriptor)
            finally:
                os.close(child)
        else:
            custody.check()
            if proof._stamp(os.stat(name, dir_fd=descriptor, follow_symlinks=False)) != before:
                raise store.WorkerStateError('Worker retirement entry changed before deletion; reference retained')
            os.unlink(name, dir_fd=descriptor)


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
            _remove_contents(child, custody, target)
            custody.check()
            current = os.stat(record['name'], dir_fd=record['parent'], follow_symlinks=False)
            if [current.st_dev, current.st_ino] != target['inode']:
                raise store.WorkerStateError('Worker retirement target changed before completion')
            os.rmdir(record['name'], dir_fd=record['parent'])
        finally:
            os.close(child)
    finally:
        close_directories(records)


def finish(home, digest, custody, *, absent_only=False):
    from hermes_platform.processes import process_record_is_dead
    from hermes_platform.runtime_health import current_instantiation_epoch

    document = pending.read_optional(home, digest, custody=custody)
    if document is None or document.get('retirement') is None:
        raise store.WorkerStateError('The authenticated worker retirement intent changed; no paths removed')
    pending._validate_retirement(document)
    targets = document['retirement']['targets']
    epoch = document['epoch'] == current_instantiation_epoch()
    present = any([target_present(target) for target in targets])
    if absent_only and present:
        raise store.WorkerStateError('Worker RAM state reappeared; original reference retained')
    if not epoch and (present or not pending._old_instantiation(document['epoch'])):
        raise store.WorkerStateError('Worker retirement belongs to another machine incarnation; existing paths retained')
    if epoch and not all(process_record_is_dead(item) for item in [*document['owners'], *document['processes']]):
        raise store.WorkerStateError('Worker retirement owner is live or unknown; every copy retained')
    with ExitStack() as stack:
        snapshot = stack.enter_context(_authenticated_snapshot(home, digest, custody, locked=True))
        if pending.read_optional(home, digest, custody=custody) != document:
            raise store.WorkerStateError('Worker pending reference changed while waiting; every copy retained')
        control = document['retirement']['control']
        companion = stack.enter_context(_authenticated_snapshot(home, control['digest'], custody))
        if (snapshot.digest != document['retirement']['archive_sha256']
                or companion.digest != control['sha256'] or companion.summary.entries != 1
                or companion.summary.bytes != control['bytes']):
            raise store.WorkerStateError('Worker checkpoint changed after retirement admission; every copy retained')
        if not present:
            outcome = 'Original RAM targets absent; last verified checkpoint retained; uncheckpointed changes unverifiable'
            check = AbsentTargets(snapshot, companion, targets)
            _complete_reference(home, digest, document, check, custody, outcome)
            return outcome
        needed = (snapshot.summary.bytes + snapshot.summary.entries*8192
                  + companion.summary.bytes*8 + companion.summary.entries*8192 + control['entries']*1024)
        base = Path(document['path']).parent
        proof.capacity(base, needed)
        stage = stack.enter_context(protected_workspace('worker-retire-proof', base=base, prove=store._prove))
        payload, controls = stage/'payload', stage/'control'
        payload.mkdir(mode=0o700)
        controls.mkdir(mode=0o700)
        snapshot.replay(payload)
        companion.replay(controls)
        guard = Survivors(document, snapshot, companion, payload, proof.load(controls, document))
        guard.verify()
        for target in targets:
            guard.check()
            _remove_target(target, guard)
        guard.check()
        _complete_reference(home, digest, document, guard, custody,
                            'Verified worker retirement completed; original checkpoint unchanged')
    return None


def _complete_reference(home, digest, document, check, custody, outcome):
    note = Path(archive.ARCHIVE_ROOT, '.recovered-'+digest+'-'+secrets.token_hex(8)+'.json')
    check.check()
    pending.write(home, note, {**document, 'outcome':outcome}, custody=custody, overwrite=False)
    check.check()
    pending.remove(home, digest, custody=custody)


def resume(home, custody):
    saved, retained = [], []
    for reference in pending.paths(home):
        document = pending.read(home, reference.relative_to(home), custody=custody)
        if document.get('retirement') is None:
            continue
        try:
            if not any([target_present(target) for target in document['retirement']['targets']]):
                outcome = finish(home, document['digest'], custody, absent_only=True)
            else:
                selected = store.WorkerResource(home, document['identity'], custody, base=Path(document['path']).parent)
                if selected.path != Path(document['path']):
                    raise store.WorkerStateError('Worker retirement resource path changed')
                with store._lock(selected.lock_path, check_alive=custody.check):
                    current = pending.read_optional(home, document['digest'], custody=custody)
                    if current is None or current.get('retirement') is None:
                        continue  # another admitted repair completed before this lock was acquired
                    outcome = finish(home, document['digest'], custody)
            selected = archive.archive_relative(document['digest']).as_posix()
            saved.append(selected if outcome is None else f'{selected}: {outcome}')
        except (VaultError, OSError, ValueError) as exc:
            retained.append(f'{reference}: {exc}; verified checkpoint and pending RAM locations retained. '
                            'Run browser.worker.repair after closing its original owners.')
    return saved, retained
