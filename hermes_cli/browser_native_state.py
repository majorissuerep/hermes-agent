"""Named native-state chronology, carried as ordinary authenticated WORKER content.

The archive format and original state-file bytes are unchanged. File mtimes are recreated during
restore, so this index preserves observed ordering. Unknown legacy chronology is never guessed.
Only a guarded producer's changed files advance ordering; maintenance merely rebases observations.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import stat

from hermes_cli import browser_worker_store as store
from hermes_security import browser_worker_archive as archive

_INDEX = '.native-session-order.json'
_MAX_INDEX = 64*1024**2


def _regular(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
            or info.st_mode & 0o022 or info.st_size > archive.MAX_STATE_BYTES):
        raise store.WorkerStateError('Native saved state is not an owned bounded regular file; all copies retained')


@contextmanager
def owned_directory(tree, components, *, create=False):
    """Open concrete native state/input components without following links."""
    with ExitStack() as stack:
        descriptor = os.open(tree, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        stack.callback(os.close, descriptor)
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise store.WorkerStateError('Native state root has unsafe ownership or permissions')
        for name in components:
            if create:
                try:
                    os.mkdir(name, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            try:
                child = os.open(name, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC, dir_fd=descriptor)
            except FileNotFoundError:
                yield None
                return
            stack.callback(os.close, child)
            info = os.fstat(child)
            if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise store.WorkerStateError('Native state directory has unsafe ownership or permissions')
            descriptor = child
        yield descriptor


def session_directory(tree, *, create=False):
    return owned_directory(tree, ('home', '.agent-browser', 'sessions'), create=create)


def file_fingerprint(directory, name, *, check_alive):
    check_alive()
    descriptor = os.open(name, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(descriptor, 'rb') as handle:
        before = os.fstat(handle.fileno())
        _regular(before)
        digest = hashlib.sha256()
        while True:
            check_alive()
            block = handle.read(65536)
            if not block:
                break
            digest.update(block)
        after = os.fstat(handle.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise store.WorkerStateError('Native saved state changed while read; original archive retained')
    check_alive()
    return {'sha256':digest.hexdigest(), 'size':before.st_size, 'mtime_ns':before.st_mtime_ns}


def _files(tree, custody):
    with session_directory(tree) as directory:
        if directory is None:
            return {}
        return {name:file_fingerprint(directory, name, check_alive=custody.check)
                for name in sorted(os.listdir(directory)) if name.endswith(('.json', '.json.enc'))}


def _read(resource):
    path = resource.tree/_INDEX
    try:
        descriptor = os.open(path, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, 'rb') as handle:
        info = os.fstat(handle.fileno())
        _regular(info)
        if info.st_size > _MAX_INDEX:
            raise store.WorkerStateError('Native saved-state order index exceeds its bound')
        document = json.load(handle)
    if (not isinstance(document, dict) or document.get('version') != 1
            or document.get('resource') != resource.digest or type(document.get('counter')) is not int
            or document['counter'] < 0 or not isinstance(document.get('entries'), dict)):
        raise store.WorkerStateError('Native saved-state order index is unreadable; all copies retained')
    for name, item in document['entries'].items():
        if (not isinstance(name, str) or Path(name).name != name or not isinstance(item, dict)
                or not isinstance(item.get('sha256'), str) or len(item['sha256']) != 64
                or any(type(item.get(key)) is not int or item[key] < 0 for key in ('size', 'mtime_ns'))
                or 'order' not in item or item['order'] is not None and (
                    type(item['order']) is not int or item['order'] < 0 or item['order'] > document['counter'])):
            raise store.WorkerStateError('Native saved-state order index contains invalid metadata')
    return document


def _write(resource, document, custody):
    custody.check()
    raw = json.dumps(document, sort_keys=True).encode()
    if len(raw) > _MAX_INDEX:
        raise store.WorkerStateError('Native saved-state order index exceeds its bound; files retained')
    store._write(resource.tree/_INDEX, document)
    custody.check()


def prepare(resource, custody):
    """Keep authenticated ordering while rebasing the current RAM filesystem observations."""
    previous = _read(resource)
    current = _files(resource.tree, custody)
    entries = {}
    for name, item in current.items():
        old = previous['entries'].get(name) if previous is not None else None
        matches = old is not None and all(old[key] == item[key] for key in ('sha256', 'size'))
        order = old.get('order') if matches else 0 if previous is None and len(current) == 1 else None
        entries[name] = {**item, 'order':order}
    document = {'version':1, 'resource':resource.digest, 'counter':previous['counter'] if previous else 0, 'entries':entries}
    _write(resource, document, custody)
    return document


def capture(resource, custody):
    """Record the actual producer delta, retaining older snapshot ordering across cold restore."""
    previous = _read(resource)
    if previous is None:
        return prepare(resource, custody)
    current = _files(resource.tree, custody)
    counter = previous['counter']
    entries = {}
    changed = {}
    for name, item in current.items():
        old = previous['entries'].get(name)
        if old is not None and all(old[key] == item[key] for key in ('sha256', 'size', 'mtime_ns')):
            entries[name] = old
        else:
            changed.setdefault(item['mtime_ns'], []).append((name, item))
    for stamp in sorted(changed):
        group = changed[stamp]
        counter += 1
        order = counter if len({item['sha256'] for _, item in group}) == 1 else None
        entries.update({name:{**item, 'order':order} for name, item in group})
    _write(resource, {'version':1, 'resource':resource.digest, 'counter':counter, 'entries':entries}, custody)


def selected(resource, name, custody):
    document = prepare(resource, custody)
    matches = [(filename, item) for filename, item in document['entries'].items() if filename.startswith(name+'-')]
    if not matches:
        return None
    if len(matches) > 1:
        if any(item['order'] is None for _, item in matches):
            raise store.UnverifiedWorkerState('Saved native session chronology is ambiguous; all original files retained for explicit recovery')
        highest = max(item['order'] for _, item in matches)
        matches = [(filename,item) for filename,item in matches if item['order'] == highest]
        if len({item['sha256'] for _, item in matches}) > 1:
            raise store.UnverifiedWorkerState('Saved native session chronology is ambiguous; original files retained')
    filename, item = sorted(matches)[0]
    return resource.tree/'home'/'.agent-browser'/'sessions'/filename, item


def verified_choice(resource, filename, custody):
    """The exact immutable input was loaded successfully; order its retained named copy now."""
    capture(resource, custody)
    document = _read(resource)
    document['counter'] += 1
    document['entries'][filename]['order'] = document['counter']
    _write(resource, document, custody)
