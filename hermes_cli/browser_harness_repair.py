"""Retire only proven-absent harness control markers during explicit WORKER repair.

No producer is signalled. Every claim/process must be dead or have this original private RAM
runtime and a native absence proof before any marker is removed. Payload files are not touched.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from hermes_cli import browser_worker_store as store
from hermes_security.errors import VaultError


def reconcile_candidates(candidates):
    from hermes_security.operation_custody import guarded
    from hermes_security.session_custody import SessionCustody

    for resource in candidates:
        if not str(resource.identity.get('kind', '')).startswith('browser-use-'):
            continue
        try:
            custody = guarded(SessionCustody.capture(resource.home, resource=resource.custody))
            with store._lock(resource.lock_path, check_alive=custody.check):
                resource._validate()
                reconcile_absent(resource, custody)
        except (VaultError, OSError, ValueError) as error:
            return [f'{resource.relative.as_posix()} ({error})']
    return []


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _absent(entry, record):
    from hermes_platform.browser_process import harness_runtime_absent

    name, shared = record.get('session'), record.get('shared')
    if (record.get('unknown') is not True or record.get('kind') != 'browser-use-harness'
            or not isinstance(name, str) or re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name) is None
            or type(shared) is not bool
            or entry.name != 'unknown-harness-'+hashlib.sha256(name.encode()).hexdigest()[:16]):
        return False
    if not isinstance(record.get('socket'), str):
        return False
    runtime = Path(record['socket'])
    if (not runtime.is_absolute() or runtime.resolve() != runtime or not store._prove(runtime).proven
            or list(store._identity(runtime)) != record.get('socket_inode')):
        return False
    stem = 'bu-'+name if shared else 'bu'
    return harness_runtime_absent(runtime/(stem+'.pid'), runtime=runtime, name=name)


def reconcile_absent(resource, custody):
    from hermes_platform.processes import process_record_is_dead

    custody.check()
    removable = []
    for folder in ('claims', 'processes'):
        for entry in (resource.path/folder).iterdir():
            custody.check()
            before = _identity(entry.lstat())
            record = store._read(entry)
            if entry.name == str(record.get('pid')) and process_record_is_dead(record):
                continue
            if folder != 'processes' or not _absent(entry, record):
                raise store.WorkerStateError('A Browser Use owner is live or unknown; close original owners and retry browser.worker.repair')
            if _identity(entry.lstat()) != before:
                raise store.WorkerStateError('Original Browser Use control changed; all state retained')
            removable.append((entry, before, record))
    # This explicit repair holds the resource lock; prove the original facts again before effects.
    for entry, before, record in removable:
        custody.check()
        resource._validate()
        if _identity(entry.lstat()) != before or not _absent(entry, record):
            raise store.WorkerStateError('Original Browser Use owner changed; every copy retained')
    for entry, before, record in removable:
        custody.check()
        resource._validate()
        if _identity(entry.lstat()) != before or not _absent(entry, record):
            raise store.WorkerStateError('Original Browser Use owner changed before retirement; state retained')
        entry.unlink()
