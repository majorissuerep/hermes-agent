"""Native bootstrap input preservation and explicit owner-authorized retry metadata.

Only files below the original owned WORKER tree are read. The exact selected bytes are retained
read-only in proven RAM before a vendor effect, then included unchanged in the ordinary WORKER
archive after verified success. A failed bootstrap blocks publication over the last good archive.
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import secrets

from hermes_cli import browser_native_state as state, browser_worker_store as store
from hermes_security import browser_worker_archive as archive

_STATUS = '.native-state-bootstrap.json'
_INPUTS = '.native-state-inputs'


def _hex(value, length):
    return isinstance(value, str) and len(value) == length and all(char in '0123456789abcdef' for char in value)


def valid_name(value):
    """Pinned native CLI name grammar; no new path syntax is accepted."""
    return isinstance(value, str) and bool(value) and all(char.isalnum() or char in '-_' for char in value)


def status(resource):
    try:
        document = store._read(resource.path/_STATUS)
    except FileNotFoundError:
        return None
    if not isinstance(document.get('input'), str):
        raise store.UnverifiedWorkerState('Native bootstrap recovery metadata has no owned input path')
    relative = Path(document['input'])
    if (document.get('version') != 1 or document.get('resource') != resource.digest
            or not valid_name(document.get('name'))
            or not _hex(document.get('sha256'), 64) or type(document.get('size')) is not int
            or document['size'] < 0 or len(relative.parts) != 3 or relative.parts[0] != _INPUTS
            or not _hex(relative.parts[1], 32) or relative.parts[2] not in {'state.json', 'state.json.enc'}
            or type(document.get('retry')) is not bool or document.get('phase') not in {'bootstrap','load','failed'}):
        raise store.UnverifiedWorkerState('Native bootstrap recovery metadata is unreadable; original archive and RAM retained')
    return document


def message(resource, document):
    return (f'Native named-state bootstrap is unverified ({document["phase"]}); original archive '
            f'{resource.home/resource.relative} and exact input {resource.tree/document["input"]} were retained. '
            'Close its browser, then run hermes manage apply with {"op":"browser.worker.repair"} and retry '
            'the original browser command. Arbitrary external import remains offline maintenance.')


def checkpoint_allowed(resource):
    document = status(resource)
    if document is not None:
        raise store.UnverifiedWorkerState(message(resource, document))


def _copy(directory, name, destination, target, expected, custody):
    custody.check()
    if directory is None or destination is None:
        raise store.UnverifiedWorkerState('Native selected state directory disappeared; previous archive retained')
    source = os.open(name, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK, dir_fd=directory)
    try:
        state._regular(os.fstat(source))
        output = os.open(target, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC, 0o600, dir_fd=destination)
        with os.fdopen(output, 'wb') as stream:
            digest, size = hashlib.sha256(), 0
            while True:
                custody.check()
                block = os.read(source, 65536)
                if not block:
                    break
                stream.write(block)
                digest.update(block)
                size += len(block)
                if size > expected['size']:
                    raise store.UnverifiedWorkerState('Native selected state changed before its immutable snapshot completed')
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o400)
        if digest.hexdigest() != expected['sha256'] or size != expected['size']:
            raise store.UnverifiedWorkerState('Native selected-state bytes changed; previous archive retained')
        os.fsync(destination)
        custody.check()
    finally:
        os.close(source)


def snapshot(resource, selected, expected, name, custody):
    custody.check()
    budget = store._capacity(resource.tree, proof=store._prove(resource.tree))
    if budget.available_bytes is None or budget.available_bytes < expected['size'] + 64*1024**2:
        raise store.UnverifiedWorkerState('Insufficient proven RAM for native bootstrap; original archive and files retained')
    relative = Path(_INPUTS, secrets.token_hex(16), 'state.json.enc' if selected.name.endswith('.enc') else 'state.json')
    with state.session_directory(resource.tree) as source, state.owned_directory(
            resource.tree, relative.parts[:-1], create=True) as destination:
        _copy(source, selected.name, destination, relative.name, expected, custody)
    document = {'version':1, 'resource':resource.digest, 'name':name, 'input':relative.as_posix(),
                'sha256':expected['sha256'], 'size':expected['size'], 'retry':False,
                'phase':'bootstrap', 'original':selected.name}
    store._write(resource.path/_STATUS, document)
    custody.check()
    return document


def verify_input(resource, document, custody):
    relative = Path(document['input'])
    with state.owned_directory(resource.tree, relative.parts[:-1]) as directory:
        if directory is None:
            raise store.UnverifiedWorkerState('Native immutable bootstrap input is missing; original archive retained')
        fingerprint = state.file_fingerprint(directory, relative.name, check_alive=custody.check)
        if any(fingerprint[key] != document[key] for key in ('sha256','size')):
            raise store.UnverifiedWorkerState('Native immutable bootstrap input changed; previous archive retained')
        descriptor = os.open(relative.name, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC, dir_fd=directory)
        try:
            state._regular(os.fstat(descriptor))
            custody.check()
            os.fchmod(descriptor, 0o400)
        finally:
            os.close(descriptor)
    return resource.tree/relative


def named_copy(resource, document, custody):
    """Keep correct bytes under a new name; never overwrite another saved state a daemon changed."""
    source_relative = Path(document['input'])
    suffix = '.json.enc' if source_relative.name.endswith('.enc') else '.json'
    filename = document['name']+'-hermes-restored-'+secrets.token_hex(8)+suffix
    with state.owned_directory(resource.tree, source_relative.parts[:-1]) as source, state.session_directory(
            resource.tree, create=True) as destination:
        _copy(source, source_relative.name, destination, filename, document, custody)
    return resource.tree/'home'/'.agent-browser'/'sessions'/filename


def newest_runtime_candidate(resource, selected, custody):
    """The vendor's implicit attempt must select the same input, never another restored mtime."""
    with state.session_directory(resource.tree) as directory:
        stamps = [os.stat(name, dir_fd=directory, follow_symlinks=False).st_mtime_ns for name in os.listdir(directory)]
        descriptor = os.open(selected.name, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC, dir_fd=directory)
        try:
            info = os.fstat(descriptor)
            state._regular(info)
            custody.check()
            os.utime(descriptor, ns=(info.st_atime_ns, max([info.st_mtime_ns, *stamps])+1))
        finally:
            os.close(descriptor)
    state.prepare(resource, custody)  # rebase observations without inventing a saved-state event


def phase(resource, document, selected_phase, custody):
    custody.check()
    document = {**document, 'phase':selected_phase, 'retry':False}
    store._write(resource.path/_STATUS, document)
    return document


def refusal(resource, document, code, stdout, stderr, custody):
    relative = Path(document['input'])
    custody.check()
    store._write(resource.tree/relative.parent/'refusal.json',
                 {'returncode':code, 'stdout':base64.b64encode(stdout).decode(),
                  'stderr':base64.b64encode(stderr).decode()})
    store._write(resource.path/_STATUS, {**document, 'phase':'failed', 'returncode':code, 'retry':False})
    raise store.UnverifiedWorkerState(message(resource, document))


def complete(resource, document, retained, custody):
    verify_input(resource, document, custody)
    state.verified_choice(resource, retained.name, custody)
    custody.check()
    (resource.path/_STATUS).unlink()


def authorize_retry(resource, custody):
    document = status(resource)
    if document is None:
        return None
    verify_input(resource, document, custody)
    archive.replay_archive(resource.home, resource.relative, custody=custody, digest=resource.digest)
    custody.check()
    store._write(resource.path/_STATUS, {**document, 'retry':True})
    return (f'Native retry authorized for retained input {resource.tree/document["input"]}; '
            f'archive {resource.home/resource.relative} unchanged. Retry the original browser command; '
            'unverified runtime files remain in RAM until that load succeeds.')
