"""Authenticated discovery of active WORKER RAM state and passive recovery history.

Only locators can identify a live RAM resource. Resolved/missing-copy records are passive history;
backup/import authenticates them but never executes their saved paths. All records use the existing
state envelope and original path AAD, never a serialized source grant or key.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import stat

from hermes_security import artifacts
from hermes_security import browser_worker_archive as archive
from hermes_security.errors import VaultIntegrityError

_BOUND = 64*1024
_LOCATOR = re.compile(r'\.pending-([0-9a-f]{64})\.json')
_RECOVERY = re.compile(r'\.recovered-([0-9a-f]{64})-[0-9a-f]{16}\.json')


def relative(digest):
    archive.archive_relative(digest)
    return Path(archive.ARCHIVE_ROOT, '.pending-'+digest+'.json')


def metadata_kind(path):
    parts = Path(path).parts
    if len(parts) != 2 or parts[0] != archive.ARCHIVE_ROOT:
        return None
    return 'locator' if _LOCATOR.fullmatch(parts[1]) else 'recovery' if _RECOVERY.fullmatch(parts[1]) else None


def paths(home, *, kind='locator'):
    root = Path(home)/archive.ARCHIVE_ROOT
    try:
        entries = sorted(os.scandir(root), key=lambda entry:entry.name)
    except FileNotFoundError:
        return []
    return [root/entry.name for entry in entries if metadata_kind(Path(archive.ARCHIVE_ROOT, entry.name)) == kind]


def _decode(raw, relative, custody):
    kind = metadata_kind(relative)
    if kind is None:
        raise PermissionError('Not an owned browser WORKER metadata path')
    if len(raw) > _BOUND+128:
        raise VaultIntegrityError('Browser WORKER metadata exceeds its bound')
    custody.check()
    plain = custody.vault.decrypt(raw, purpose='state', relpath=Path(relative).as_posix(), authority=custody.authority)
    document = json.loads(plain)
    matched = (_LOCATOR if kind == 'locator' else _RECOVERY).fullmatch(Path(relative).name)
    if (not isinstance(document, dict) or type(document.get('version')) is not int
            or document['version'] not in (1, 2) or document.get('digest') != matched[1]
            or not isinstance(document.get('identity'), dict)
            or archive.identity_digest(custody, document['identity']) != document['digest']):
        raise VaultIntegrityError('Browser WORKER metadata has invalid original identity')
    if kind == 'locator' and document.get('home') != str(custody.vault.home):
        raise VaultIntegrityError('Browser WORKER locator belongs to another profile')
    _validate(document, kind)
    custody.check()
    return document


def _inode(value):
    return isinstance(value, list) and len(value) == 2 and all(type(item) is int and item >= 0 for item in value)


def _validate(document, kind):
    absolute = all(isinstance(document.get(name), str) and Path(document[name]).is_absolute()
                   for name in ('home', 'path'))
    if (not absolute or not _inode(document.get('root_inode')) or not isinstance(document.get('epoch'), str)
            or not isinstance(document.get('owners'), list) or not isinstance(document.get('processes'), list)):
        raise VaultIntegrityError('Browser WORKER reference metadata is incomplete')
    for record in [*document['owners'], *document['processes']]:
        if not isinstance(record, dict):
            raise VaultIntegrityError('Browser WORKER process metadata is incomplete')
        if record.get('unknown') is True:
            valid = (isinstance(record.get('session'), str) and isinstance(record.get('executable'), str)
                     and isinstance(record.get('socket'), str) and _inode(record.get('socket_inode')))
        else:
            valid = all(type(record.get(key)) is int and record[key] > 0 for key in ('pid', 'start_time'))
        if not valid:
            raise VaultIntegrityError('Browser WORKER process metadata is invalid')
    if kind == 'recovery' and not isinstance(document.get('outcome'), str):
        raise VaultIntegrityError('Browser WORKER recovery history is incomplete')
    retirement = document.get('retirement')
    if (document['version'] == 2) != (retirement is not None):
        raise VaultIntegrityError('Worker retirement requires its closed metadata version; every copy retained')
    if retirement is not None:
        _validate_retirement(document)


def _validate_retirement(document):
    intent = document['retirement']
    expected = {document['path']}
    external = document['identity'].get('external_tree')
    if external is not None:
        if not isinstance(external, str) or not Path(external).is_absolute():
            raise VaultIntegrityError('Worker retirement has an invalid explicit path')
        expected.add(external)
    if (not isinstance(intent, dict) or not isinstance(intent.get('archive_sha256'), str)
            or len(intent['archive_sha256']) != 64
            or any(char not in '0123456789abcdef' for char in intent['archive_sha256'])
            or not isinstance(intent.get('targets'), list) or len(intent['targets']) != len(expected)):
        raise VaultIntegrityError('Worker retirement intent is incomplete; every copy retained')
    seen = set()
    for target in intent['targets']:
        if (not isinstance(target, dict) or not isinstance(target.get('path'), str) or target['path'] not in expected
                or not Path(target['path']).is_absolute() or not _inode(target.get('inode'))):
            raise VaultIntegrityError('Worker retirement target is outside its authenticated resource')
        if target['path'] == document['path'] and target['inode'] != document['root_inode']:
            raise VaultIntegrityError('Worker retirement root changed its original identity')
        seen.add(target['path'])
    if seen != expected:
        raise VaultIntegrityError('Worker retirement targets are incomplete')


def read(home, relative, *, custody):
    raw = artifacts._read_owned_bytes(Path(home), Path(relative), _BOUND+128)
    return _decode(raw, relative, custody)


def read_optional(home, digest, *, custody):
    try:
        return read(home, relative(digest), custody=custody)
    except FileNotFoundError:
        return None


def authenticate_handle(handle, relative, *, custody):
    info = os.fstat(handle.fileno())
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or info.st_size > _BOUND+128):
        raise PermissionError('Browser WORKER metadata must be a bounded private regular file')
    return _decode(handle.read(_BOUND+129), relative, custody)


def write(home, relative, document, *, custody, overwrite=True):
    if metadata_kind(relative) is None:
        raise PermissionError('Not an owned browser WORKER metadata path')
    plain = json.dumps(document, sort_keys=True).encode()
    if len(plain) > _BOUND:
        raise VaultIntegrityError('Browser WORKER metadata exceeds its bound')
    custody.check()
    encoded = custody.vault.encrypt(plain, purpose='state', relpath=Path(relative).as_posix(), authority=custody.authority)
    artifacts._publish(Path(home), Path(relative), encoded, custody, overwrite=overwrite)


def sync(resource, call):
    from hermes_platform.runtime_health import current_instantiation_epoch

    previous = read_optional(resource.home, resource.digest, custody=call)
    if previous is not None and previous.get('retirement') is not None:
        raise VaultIntegrityError('Verified worker retirement is pending; use browser.worker.repair')
    records = {}
    for kind in ('claims', 'processes'):
        records[kind] = [resource.read_record(path) for path in sorted((resource.path/kind).iterdir())]
    document = {'version':1, 'home':str(resource.home), 'digest':resource.digest, 'identity':resource.identity,
                'path':str(resource.path), 'root_inode':list(call.root_identity),
                'epoch':current_instantiation_epoch(), 'owners':records['claims'], 'processes':records['processes']}
    write(resource.home, relative(resource.digest), document, custody=call)


def remove(home, digest, *, custody):
    selected = relative(digest)
    with artifacts._parent(Path(home), selected) as parent:
        custody.check()
        os.unlink(selected.name, dir_fd=parent)
        os.fsync(parent)


def note_missing(home, document, *, custody):
    """Explicit repair retains the original reference as passive history, then releases active debt."""
    selected = Path(archive.ARCHIVE_ROOT, '.recovered-'+document['digest']+'-'+secrets.token_hex(8)+'.json')
    note = {**document, 'outcome':'RAM copy missing; last authenticated checkpoint retained; uncheckpointed changes unverifiable'}
    write(home, selected, note, custody=custody, overwrite=False)
    remove(home, document['digest'], custody=custody)
    return selected


def root_missing(document):
    """Only kernel ENOENT establishes absence; permission errors never become an empty copy."""
    try:
        Path(document['path']).lstat()
    except FileNotFoundError:
        return True
    return False


def _old_instantiation(epoch):
    from hermes_platform.runtime_health import current_instantiation_epoch

    current = current_instantiation_epoch()
    return (all(len(value.split(':')) == 2 and all(value.split(':')) for value in (epoch, current))
            and epoch != current)


def recover_missing(home, *, custody):
    """Explicit human repair releases missing RAM debt only after authenticating its last checkpoint.

    Every original process must be known dead, or a complete kernel/container epoch must have changed.
    The passive history preserves the missing-copy reference and explicitly records uncertain changes.
    """
    from hermes_platform.processes import process_record_is_dead

    resolved, retained = [], []
    for reference in paths(home):
        document = read(home, reference.relative_to(home), custody=custody)
        if document.get('retirement') is not None:
            retained.append(f'{reference}: verified worker retirement is pending; run browser.worker.repair. '
                            'Original target locations and checkpoint retained.')
            continue
        if not root_missing(document):
            continue
        records = [*document['owners'], *document['processes']]
        if not _old_instantiation(document['epoch']) and not all(process_record_is_dead(item) for item in records):
            retained.append(f'{reference}: RAM missing at {document["path"]}; original owners are live or unknown. '
                            'Close the original owners and rerun hermes secure-vault repair. '
                            'Keep the full encrypted backup if the original environment cannot be inspected.')
            continue
        relative_archive = archive.archive_relative(document['digest'])
        archive.replay_archive(home, relative_archive, custody=custody)
        history = note_missing(home, document, custody=custody)
        resolved.append(f'{history}: RAM missing at {document["path"]}; last authenticated checkpoint '
                        f'{relative_archive} retained; uncheckpointed changes unverifiable')
    return resolved, retained
