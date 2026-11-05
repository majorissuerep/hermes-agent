"""Explicit IPC/log roots stay at their chosen path only on proven private RAM."""
from __future__ import annotations

import os
from pathlib import Path
import stat

from hermes_cli import browser_worker_store as store
from tools.browser_native_paths import _directory

_MARKER = '.hermes-browser-harness-runtime.json'


def _create(path):
    """Clear inherited SGID only on a newly created pinned child, never an operator directory."""
    parent = os.open(path.parent, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        info = os.fstat(parent)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise store.WorkerStateError('An explicit runtime needs a non-writable owned parent; no permissions changed')
        try:
            os.mkdir(path.name, 0o700, dir_fd=parent)
        except FileExistsError:
            return
        child = os.open(path.name, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC, dir_fd=parent)
        try:
            current = os.fstat(child)
            if current.st_uid != os.geteuid() or stat.S_IMODE(current.st_mode) not in {0o700,0o2700}:
                raise store.WorkerStateError('Fresh browser runtime identity changed; every copy retained')
            os.fchmod(child, 0o700)
            after = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (after.st_dev,after.st_ino) != (current.st_dev,current.st_ino):
                raise store.WorkerStateError('Fresh browser runtime was replaced; every copy retained')
        finally:
            os.close(child)
    finally:
        os.close(parent)


def _scope(home, path):
    for parent in (path, *path.parents):
        markers = []
        if parent.name.startswith(store.PREFIX):
            markers.append(parent/store._BINDING)
        markers.extend(parent/name for name in ('.hermes-browser-worker-owner.json', _MARKER)
                       if os.path.lexists(parent/name))
        for marker in markers:
            if store._read(marker).get('home') != str(home):
                raise store.WorkerStateError('Explicit runtime is inside another profile resource; every copy retained')


class RuntimePaths:
    def __init__(self, home, resource, values):
        self.home, self.resource = home, resource
        self.values = dict(values)
        self.aliases, self.original = {}, {}
        for key in ('BH_RUNTIME_DIR','BH_TMP_DIR'):
            if values.get(key):
                requested = Path(values[key]).expanduser().absolute()
                self.aliases[key] = requested, requested.resolve()

    def check(self):
        for alias, selected in self.aliases.values():
            if alias.resolve() != selected:
                raise store.WorkerStateError('Original Browser Use runtime alias changed; every copy retained')
        for path, identity in self.original.items():
            if _directory(path, strict=True) != identity:
                raise store.WorkerStateError('Original Browser Use runtime was replaced; every copy retained')

    def prepare(self, key, fallback, check):
        selected_key = key if key in self.aliases else 'BH_TMP_DIR' if key == 'BH_RUNTIME_DIR' else key
        if selected_key not in self.aliases:
            return fallback
        check()
        _alias, path = self.aliases[selected_key]
        _scope(self.home, path)
        if not store._prove(path).proven:
            raise store.WorkerStateError('Explicit Browser Use runtime/log directory is not proven RAM; choose a private RAM directory')
        try:
            path.lstat()
        except FileNotFoundError:
            check()
            _create(path)
        identity = _directory(path, strict=True)
        expected = {'version':1, 'home':str(self.home), 'resource':self.resource.digest}
        marker = path/_MARKER
        if os.path.lexists(marker):
            if store._read(marker) != expected:
                raise store.WorkerStateError('Explicit Browser Use runtime belongs to another profile/resource; contents retained')
        else:
            if any(path.iterdir()):
                raise store.WorkerStateError('Untracked Browser Use runtime data retained; choose a new empty private RAM directory')
            check()
            store._write(marker, expected)
        if _directory(path, strict=True) != identity:
            raise store.WorkerStateError('Explicit Browser Use runtime changed during binding; every copy retained')
        self.original[path] = identity
        check()
        return path

    def shared(self, key):
        return '1' if self.values.get(key) == '1' else '0'
