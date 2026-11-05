"""Verify current survivors against full authenticated payload and hash-only control proof."""
from __future__ import annotations

import os
from pathlib import Path
import stat

from hermes_cli import browser_worker_store as store
from hermes_cli import browser_worker_retirement_proof as proof
from hermes_security import tree_archive


def target_present(target):
    """Only native ENOENT is absence; aliases and an existing replaced parent refuse."""
    path = Path(target['path'])
    if path.resolve() != path:
        raise store.WorkerStateError('Worker retirement target has an unresolved alias; every copy retained')
    try:
        parent = path.parent.stat()
    except FileNotFoundError:
        return False
    if [parent.st_dev,parent.st_ino] != target['parent_inode']:
        raise store.WorkerStateError('Original worker target parent was replaced; every copy retained')
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


class AbsentTargets:
    def __init__(self, saved, control, targets):
        self.saved, self.control, self.targets = saved, control, targets

    def check(self):
        self.saved.check()
        self.control.check()
        if any([target_present(target) for target in self.targets]):
            raise store.WorkerStateError('Worker RAM state reappeared; pending reference and every copy retained')


class Survivors:
    def __init__(self, document, saved, control, reference, entries):
        self.document, self.saved, self.control = document, saved, control
        self.reference, self.entries = reference, entries
        self.root = Path(document['path'])
        self.payload = Path(document['identity'].get('external_tree', str(self.root/'tree')))
        self.expected_hashes = {}

    def check(self):
        self.saved.check()
        self.control.check()
        for target in self.document['retirement']['targets']:
            path = Path(target['path'])
            if not target_present(target):
                continue
            current = path.lstat()
            if (not stat.S_ISDIR(current.st_mode) or current.st_uid != os.geteuid()
                    or stat.S_IMODE(current.st_mode) != 0o700
                    or [current.st_dev,current.st_ino] != target['inode'] or path.resolve() != path):
                raise store.WorkerStateError('Original worker target was replaced; every copy retained')

    def _payload_entry(self, relative, parent, name, info):
        kind = stat.S_IFMT(info.st_mode)
        if kind not in (stat.S_IFDIR,stat.S_IFREG) or info.st_uid != os.geteuid():
            raise store.WorkerStateError('Unknown worker payload type or owner; every copy retained')
        if not relative.parts:
            return proof._stamp(info)
        try:
            with tree_archive._source_directory(self.reference, 'verified worker payload', relative.parent.as_posix()) as original:
                expected = os.stat(relative.name, dir_fd=original, follow_symlinks=False)
                if stat.S_IFMT(expected.st_mode) != kind:
                    raise store.WorkerStateError('Worker payload survivor changed type')
                if kind == stat.S_IFDIR:
                    return proof._stamp(info)
                if (info.st_size != expected.st_size
                        or bool(info.st_mode & stat.S_IXUSR) != bool(expected.st_mode & stat.S_IXUSR)):
                    raise store.WorkerStateError('Worker payload survivor changed supported attributes')
                key = relative.as_posix()
                if key not in self.expected_hashes:
                    self.expected_hashes[key] = proof.file_hash(original, relative.name, self.check)[0]
                actual, after = proof.file_hash(parent, name, self.check)
                if actual != self.expected_hashes[key]:
                    raise store.WorkerStateError('Worker payload survivor changed content; every copy retained')
                return proof._stamp(after)
        except FileNotFoundError as exc:
            raise store.WorkerStateError('New worker payload has no verified checkpoint copy; every copy retained') from exc

    def entry(self, target, relative, parent, name):
        """Check one exact current pathname and return its stable native fingerprint."""
        self.check()
        path = Path(target['path'])/relative
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if path == self.payload or path.is_relative_to(self.payload):
            verified = self._payload_entry(path.relative_to(self.payload), parent, name, info)
            if path != self.payload or path == Path(target['path']):
                if proof._stamp(os.stat(name, dir_fd=parent, follow_symlinks=False)) != verified:
                    raise store.WorkerStateError('Worker payload changed after hash verification; every copy retained')
                return verified
        if path == self.root:
            return proof._stamp(info)
        key = path.relative_to(self.root).as_posix()
        if key not in self.entries or proof.control_record(parent, name, self.check) != self.entries[key]:
            raise store.WorkerStateError('New or changed worker control state was retained; no unverified cleanup')
        after = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if proof._stamp(info) != proof._stamp(after):
            raise store.WorkerStateError('Worker control changed during its verification; every copy retained')
        return proof._stamp(after)

    def walk(self, target, descriptor, prefix=Path()):
        for name in sorted(os.listdir(descriptor)):
            relative = prefix/name
            before = self.entry(target, relative, descriptor, name)
            if stat.S_ISDIR(before[5]):
                child = os.open(name, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC, dir_fd=descriptor)
                try:
                    if proof._stamp(os.fstat(child)) != before:
                        raise store.WorkerStateError('Worker survivor directory changed during verification')
                    self.walk(target, child, relative)
                finally:
                    os.close(child)

    def verify(self):
        """Preflight the whole remainder before the first destructive effect."""
        self.check()
        for target in self.document['retirement']['targets']:
            try:
                descriptor = os.open(target['path'], os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
            except FileNotFoundError:
                continue
            try:
                self.check()
                self.walk(target, descriptor)
            finally:
                os.close(descriptor)
        self.check()
