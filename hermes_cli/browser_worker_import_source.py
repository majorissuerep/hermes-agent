"""Retire an explicitly selected legacy source only against its full authenticated copy."""
from __future__ import annotations

import os
from pathlib import Path
import stat

from hermes_cli import browser_worker_store as store, browser_worker_retirement as retirement
from hermes_cli.browser_worker_retirement_survivors import Survivors, target_present


class ImportedSource(Survivors):
    """Payload-only proof for offline import; normal WORKER retirement stays RAM-only."""
    def __init__(self, source, requested, custody, *, original=None):
        self.requested = Path(requested).expanduser().absolute()
        self.payload = self.root = source
        self.current = None
        self.operation = self.saved = self.control = custody
        self.reference, self.entries, self.expected_hashes = None, {}, {}
        info = source.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise store.WorkerStateError('Import source must be owned and not writable by other users; original retained')
        parent = source.parent.stat()
        captured = {'mode':stat.S_IMODE(info.st_mode), 'inode':[info.st_dev,info.st_ino],
                    'parent_inode':[parent.st_dev,parent.st_ino]}
        original = captured if original is None else original
        self.mode = original['mode']
        self.target = {'path':str(source), 'inode':original['inode'], 'parent_inode':original['parent_inode']}
        self.document = {'retirement':{'targets':[self.target]}}

    @property
    def home(self):
        return self.operation.home

    @property
    def vault(self):
        return self.operation.vault

    @property
    def authority(self):
        return self.operation.authority

    def derive_key(self, purpose):
        self.check()
        return self.operation.derive_key(purpose)

    def check_target(self, path):
        self.check()
        return self.operation.check_target(path)

    def aad_path(self, relative):
        self.check()
        return self.operation.aad_path(relative)

    def encrypt(self, payload, *, purpose, relpath):
        self.check()
        return self.operation.encrypt(payload, purpose=purpose, relpath=relpath)

    def decrypt(self, payload, *, purpose, relpath):
        self.check()
        return self.operation.decrypt(payload, purpose=purpose, relpath=relpath)

    def bind_snapshot(self, saved, reference):
        self.check()
        self.saved = self.control = saved
        self.reference = reference
        self.check()

    def check(self):
        self.saved.check()
        if self.current is not None:
            self.current.check()
        if self.requested.resolve() != self.root or not target_present(self.target):
            raise store.WorkerStateError('Original import source/alias disappeared or changed; verified copies retained')
        current = self.root.lstat()
        if (not stat.S_ISDIR(current.st_mode) or current.st_uid != os.geteuid()
                or [current.st_dev,current.st_ino] != self.target['inode']
                or stat.S_IMODE(current.st_mode) != self.mode):
            raise store.WorkerStateError('Original import source identity changed; verified copies retained')

    def retire(self):
        self.verify()
        parent = os.open(self.root.parent, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            self.check()
            child = os.open(self.root.name, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC, dir_fd=parent)
            try:
                info = os.fstat(child)
                if [info.st_dev,info.st_ino] != self.target['inode']:
                    raise store.WorkerStateError('Import source changed before retirement; every copy retained')
                retirement._remove_contents(child, self, self.target)
                self.check()
                named = os.stat(self.root.name, dir_fd=parent, follow_symlinks=False)
                if [named.st_dev,named.st_ino] != self.target['inode']:
                    raise store.WorkerStateError('Import source changed before final retirement; every copy retained')
                os.rmdir(self.root.name, dir_fd=parent)
            finally:
                os.close(child)
        finally:
            os.close(parent)
