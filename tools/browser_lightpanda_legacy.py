"""Explicit literal Lightpanda SSD-state import, using the existing WORKER source proof.

All original files, including old logs/control, are authenticated as data. No saved PID
record is ever revived as current execution authority. A retry uses the original full
snapshot, never replaces it with a partial remainder.
"""
from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import secrets

from hermes_cli import browser_worker_store as store, browser_worker_retirement as retirement
from hermes_cli.browser_worker_import_source import ImportedSource
from hermes_platform.lightpanda_process import require_legacy_idle
from hermes_platform.runtime_health import current_instantiation_epoch
from hermes_security import browser_worker_archive as archive
from hermes_security.operation_custody import guarded
from hermes_security.protected_workspace import fresh_sibling_workspace, remove_stable_workspace
from hermes_security.session_custody import SessionCustody
from tools.browser_lightpanda_state import KIND, legacy_path

_MARKER = '.lightpanda-original-import.json'


def _validate(document, selected, source):
    from hermes_security.browser_worker_pending import _inode

    native = document.get('native') if isinstance(document, dict) else None
    if (not isinstance(document, dict) or document.get('version') != 1
            or document.get('home') != str(selected.home) or document.get('source') != str(source)
            or document.get('resource') != selected.digest or not isinstance(native, dict)
            or not _inode(native.get('inode')) or not _inode(native.get('parent_inode'))
            or type(native.get('mode')) is not int or native['mode'] & 0o022
            or not isinstance(document.get('epoch'), str)
            or archive.worker_state_kind(Path(document.get('recovery', ''))) != 'archive'):
        raise store.WorkerStateError('Original Lightpanda import provenance is invalid; every copy retained')
    return document


def _new(home, selected, source, operation, stage):
    if os.path.lexists(source/_MARKER):
        raise store.WorkerStateError('Legacy Lightpanda source contains reserved import metadata; retained')
    original = ImportedSource(source, source, operation)
    require_legacy_idle(source, check_alive=original.check)
    identity = {'kind':'lightpanda-original-source', 'source':str(source), 'nonce':secrets.token_hex(16)}
    digest = archive.identity_digest(operation, identity)
    relative = archive.archive_relative(digest)
    archive.write_archive(home, relative, source, custody=original, identity=identity, digest=digest)
    with retirement._authenticated_snapshot(home, digest, operation) as saved:
        store._require_restore_capacity(selected.base, saved.summary)
        saved.replay(stage/'tree')
        original.bind_snapshot(saved, stage/'tree')
        if archive.tree_digest(source) != saved.summary:
            raise store.WorkerStateError('Legacy Lightpanda source changed during full verification; retained')
        document = {'version':1, 'home':str(home), 'source':str(source), 'resource':selected.digest,
                    'recovery':relative.as_posix(), 'epoch':current_instantiation_epoch(),
                    'native':{'mode':original.mode, 'inode':original.target['inode'],
                              'parent_inode':original.target['parent_inode']}}
        store._write(stage/'tree'/_MARKER, document)
        archive.write_archive(home, selected.relative, stage/'tree', custody=original,
                              identity=selected.identity, digest=selected.digest)
    return document


def _existing(home, selected, source, operation, stage):
    summary = archive.replay_archive(home, selected.relative, custody=operation)
    store._require_restore_capacity(selected.base, summary)
    archive.replay_archive(home, selected.relative, custody=operation, target=stage/'tree')
    return _validate(store._read(stage/'tree'/_MARKER), selected, source)


def _retire(home, selected, source, operation, stage, document):
    if document['epoch'] != current_instantiation_epoch():
        raise store.WorkerStateError('Original Lightpanda source belongs to another native incarnation; retained')
    with ExitStack() as stack:
        saved = stack.enter_context(retirement._authenticated_snapshot(home, Path(document['recovery']).stem, operation))
        current = stack.enter_context(retirement._authenticated_snapshot(home, selected.digest, operation))
        original = ImportedSource(source, source, operation, original=document['native'])
        original.bind_snapshot(saved, stage/'tree')
        original.current = current
        verified = stage/'original'; verified.mkdir(mode=0o700)
        store._require_restore_capacity(selected.base, saved.summary)
        saved.replay(verified)
        original.reference = verified
        original.check()
        require_legacy_idle(source, check_alive=original.check)
        original.retire()  # Existing pinned source/parent, full-preflight and per-entry survivor hashes.
        current.check()


def import_literal(home):
    """Only an explicit worker.repair request admits this exact owned historic root."""
    home = Path(home).resolve()
    source = legacy_path(home)
    if source.resolve() != source or source.parent.resolve() != source.parent:
        raise store.WorkerStateError('Legacy Lightpanda source has an alias; original retained')
    selected = store.resource(home, kind=KIND)
    operation = guarded(SessionCustody.capture(home, resource=selected.custody))
    with store._lock(selected.lock_path, check_alive=operation.check):
        if os.path.lexists(selected.path):
            selected._validate()
            store._require_idle(selected)
            raise store.WorkerStateError('An existing RAM Lightpanda cache needs explicit worker repair first; both copies retained')
        stage = fresh_sibling_workspace(selected.path, 'import', prove=store._prove)
        for name in ('tree','r','tmp','claims','processes'):
            (stage/name).mkdir(mode=0o700)
        try:
            exists = archive.archive_ciphertext_size(home, selected.relative, digest=selected.digest) is not None
            document = (_existing(home, selected, source, operation, stage) if exists
                        else _new(home, selected, source, operation, stage))
            _retire(home, selected, source, operation, stage, document)
            operation.check()
            selected.bind(stage)
            os.rename(stage, selected.path)
            return {'archive':selected.relative.as_posix(), 'recovery':document['recovery']}
        except (OSError, RuntimeError, ValueError) as error:
            raise store.WorkerStateError(
                f'Lightpanda import incomplete: original remainder {source}; full encrypted mapping '
                f'{home/selected.relative}. Keep both and repeat the same explicit browser.worker.repair. '
                f'No new or changed survivor is discarded: {error}') from error
        finally:
            if stage.exists():
                remove_stable_workspace(stage)
