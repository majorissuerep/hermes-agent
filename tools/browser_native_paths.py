"""Prepared native browser directories, pinned before any client can write private bytes.

This is the native producer's concrete environment admission, not protection against an arbitrary
same-user filesystem actor. New vendor auth/session directories may appear inside the original
private HOME; an observed directory may never silently become a different inode or link.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat

from hermes_cli import browser_worker_store as store

_DIRECTORIES = ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME',
                'TMPDIR', 'TMP', 'TEMP', 'AGENT_BROWSER_SOCKET_DIR')


def _directory(path, *, strict):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or (stat.S_IMODE(info.st_mode) != 0o700 if strict else bool(info.st_mode & 0o022))
            or path.resolve() != path or not store._prove(path).proven):
        raise store.WorkerStateError('Native browser private directory is linked, replaced, or outside proven RAM')
    return info.st_dev, info.st_ino


class PrivateDirectories:
    def __init__(self, values):
        self.values = {key:values[key] for key in _DIRECTORIES}
        self.required = {Path(value):_directory(Path(value), strict=True) for value in self.values.values()}
        root = Path(values['HOME'])/'.agent-browser'
        self.optional = {path:None for path in (root, root/'auth', root/'sessions')}
        self.check(values)

    def check(self, values):
        if any(values.get(key) != value for key, value in self.values.items()):
            raise store.WorkerStateError('The prepared native browser environment changed its private directory selection')
        if any(_directory(path, strict=True) != expected for path, expected in self.required.items()):
            raise store.WorkerStateError('The original native browser private directory inode was replaced')
        for path, expected in self.optional.items():
            try:
                current = _directory(path, strict=False)
            except FileNotFoundError:
                if expected is not None:
                    raise store.WorkerStateError('The original native browser auth/session directory disappeared') from None
                continue
            if expected is not None and current != expected:
                raise store.WorkerStateError('The original native browser auth/session directory inode was replaced')
            self.optional[path] = current
