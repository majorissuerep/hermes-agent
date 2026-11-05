"""Deliberate Browser Use source conversion using the established WORKER tree codec.

No module in an imported workspace is executed here. Full original bytes and owner-execute
metadata are authenticated in RAM before deletion; an interrupted retry uses the original full
recovery archive and original native source identity, never reseals a partial source.
"""
from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import re
import secrets

from hermes_cli import browser_worker_store as store, browser_worker_retirement as retirement
from hermes_cli.browser_worker_import_source import ImportedSource
from hermes_platform.browser_process import require_harness_source_idle
from hermes_platform.runtime_health import current_instantiation_epoch
from hermes_security import browser_worker_archive as archive
from hermes_security.errors import VaultError
from hermes_security.protected_workspace import fresh_sibling_workspace, remove_stable_workspace
from hermes_security.session_custody import SessionCustody

MARKER = '.hermes-browser-use-import.json'
_KINDS = {'workspace', 'config'}


def canonical_source(selected):
    return Path(selected).expanduser().absolute(), Path(selected).expanduser().resolve()


def mapped_resource(home, source, kind):
    if kind not in _KINDS:
        raise ValueError('Browser Use imports support workspace or config only')
    return store.resource(home, kind='browser-use-imported-tree', task=str(source))


def _validate_source(home, source, kind, task):
    from hermes_security.io import _home_for
    from hermes_security.profile_binding import for_file

    probe = source/'browser-use-import-probe.json'
    owner = for_file(probe).home if _home_for(probe) is not None else None
    legacy = home/'cache'/'browser-use'/'workspace'
    if source == home or home.is_relative_to(source) or (owner is not None and owner != home):
        raise store.WorkerStateError('Browser Use source belongs to an encrypted home or another profile')
    if source.is_relative_to(home) and not (kind == 'workspace' and source.parent == legacy):
        raise store.WorkerStateError('Only the precise old Browser Use workspace root may be imported from its own home')
    if source.parent == legacy:
        task = 'default' if task is None and source.name == 'default' else task
        safe = re.sub(r'[^A-Za-z0-9._-]+', '_', str(task))[:80] if task is not None else None
        if safe in {None, '', '.', '..'} or safe != source.name:
            raise store.WorkerStateError('Legacy workspace import requires its exact original --browser-use-task; ambiguous names retained')
    if (source/'.hermes-vault').exists() or source == source.parent:
        raise store.WorkerStateError('An encrypted home or filesystem root is not a Browser Use source')
    return task


def cmd_import(args, home):
    """Import only the explicitly selected tree, including authenticated machine-key profiles."""
    from hermes_cli.vault_cmd import gate_locked_vault
    from hermes_security.profile_binding import owner_hint
    from hermes_state.runtime_ownership import OwnershipConflict, exclusive_maintenance

    home = Path(home).resolve()
    try:
        owner = owner_hint(home)
        with exclusive_maintenance([home]):
            gate_locked_vault(args, owner)
            workspace = getattr(args, 'browser_use_workspace', None)
            selected = workspace or getattr(args, 'browser_use_config', None)
            imported = import_source(home, selected, kind='workspace' if workspace else 'config',
                                     task=getattr(args, 'browser_use_task', None))
    except (OwnershipConflict, OSError, ValueError, VaultError) as error:
        print(f'✗ Browser Use import refused; original data and recovery copies retained: {error}')
        return 1
    print(f"✓ Browser Use source moved into {imported['archive']} (helper code was not executed)")
    print(f"  Original-source encrypted recovery: {imported['recovery']}")
    print(f"  Protected RAM mapping: {imported['workspace']} (recreated there when needed)")
    return 0


def _validate_marker(document, selected, source, kind):
    from hermes_security.browser_worker_pending import _inode

    native = document.get('native') if isinstance(document, dict) else None
    if (not isinstance(document, dict) or document.get('version') != 1
            or document.get('home') != str(selected.home) or document.get('source') != str(source)
            or document.get('kind') not in _KINDS or document.get('resource') != selected.digest
            or not isinstance(native, dict) or not _inode(native.get('inode')) or not _inode(native.get('parent_inode'))
            or type(native.get('mode')) is not int or not 0 <= native['mode'] <= 0o7777
            or native['mode'] & 0o022 or not isinstance(document.get('epoch'), str)):
        raise store.WorkerStateError('Original Browser Use import provenance is invalid; every copy retained')
    recovery = Path(document.get('recovery', ''))
    if archive.worker_state_kind(recovery) != 'archive':
        raise store.WorkerStateError('Original Browser Use recovery checkpoint is invalid; every copy retained')
    return document


def check_mapping(selected, requested, *, kind, task=None):
    requested, source = canonical_source(requested)
    document = _validate_marker(store._read(selected.tree/MARKER), selected, source, kind)
    if task is not None and document.get('task') != str(task):
        raise store.WorkerStateError('The old sanitized workspace name is ambiguous for this task; original import retained')
    if os.path.lexists(source):
        raise store.WorkerStateError(
            f'Original Browser Use source reappeared at {requested}; both copies retained. Close its owners '
            'and rerun hermes secure-vault repair with the same explicit browser-use import selector.')
    return document


def _new_source(home, source, requested, kind, task, selected, operation, stage):
    if os.path.lexists(source/MARKER):
        raise store.WorkerStateError('Browser Use source contains a reserved import record; original bytes retained')
    original = ImportedSource(source, requested, operation)
    require_harness_source_idle(source, check_alive=original.check)
    identity = {'kind':'browser-use-source-recovery', 'source':str(source), 'import':kind, 'nonce':secrets.token_hex(16)}
    digest = archive.identity_digest(operation, identity)
    recovery = archive.archive_relative(digest)
    archive.write_archive(home, recovery, source, custody=original, identity=identity, digest=digest)
    with retirement._authenticated_snapshot(home, digest, operation) as saved:
        store._require_restore_capacity(selected.base, saved.summary)
        saved.replay(stage/'tree')
        original.bind_snapshot(saved, stage/'tree')
        if archive.tree_digest(source) != saved.summary:
            raise store.WorkerStateError('Browser Use source changed during verification; full encrypted recovery retained')
        original.check()
        document = {'version':1, 'home':str(home), 'kind':kind, 'source':str(source), 'alias':str(requested),
                    'resource':selected.digest, 'task':None if task is None else str(task),
                    'native':{'mode':original.mode, 'inode':original.target['inode'],
                              'parent_inode':original.target['parent_inode']},
                    'epoch':current_instantiation_epoch(), 'recovery':recovery.as_posix()}
        store._write(stage/'tree'/MARKER, document)
        archive.write_archive(home, selected.relative, stage/'tree', custody=original,
                              identity=selected.identity, digest=selected.digest)
    return document


def _existing_source(home, selected, source, kind, operation, stage):
    summary = archive.replay_archive(home, selected.relative, custody=operation)
    store._require_restore_capacity(selected.base, summary)
    archive.replay_archive(home, selected.relative, custody=operation, target=stage/'tree')
    return _validate_marker(store._read(stage/'tree'/MARKER), selected, source, kind)


def _finish(home, selected, source, requested, document, operation, stage):
    digest = Path(document['recovery']).stem
    with ExitStack() as stack:
        full = stack.enter_context(retirement._authenticated_snapshot(home, digest, operation))
        current = stack.enter_context(retirement._authenticated_snapshot(home, selected.digest, operation))
        if not os.path.lexists(source):
            # No source effects: the authenticated selected mapping is usable at its explicit path.
            operation.check()
            return
        if document['epoch'] != current_instantiation_epoch():
            raise store.WorkerStateError('Original import source belongs to another machine incarnation; every copy retained')
        original = ImportedSource(source, requested, operation, original=document['native'])
        original.bind_snapshot(full, stage/'tree')
        original.current = current
        # The original recovery is also replayed without rewriting current/source bytes.
        verified = stage/'original'; verified.mkdir(mode=0o700)
        store._require_restore_capacity(selected.base, full.summary)
        full.replay(verified)
        original.reference = verified
        original.check()
        current.check()
        require_harness_source_idle(source, check_alive=original.check)
        original.retire()
        current.check()


def import_source(home, requested, *, kind, task=None):
    """Exclusive offline caller only: save, verify, and deliberately retire one selected source."""
    home = Path(home).resolve()
    requested, source = canonical_source(requested)
    task = _validate_source(home, source, kind, task)
    selected = mapped_resource(home, source, kind)
    from hermes_security.operation_custody import guarded

    operation = guarded(SessionCustody.capture(home, resource=selected.custody))
    with store._lock(selected.lock_path, check_alive=operation.check):
        if os.path.lexists(selected.path):
            selected._validate()
            store._require_idle(selected)
            raise store.WorkerStateError('An owned Browser Use copy already exists in RAM; close it and run browser.worker.repair first')
        stage = fresh_sibling_workspace(selected.path, 'import', prove=store._prove)
        for name in ('tree', 'r', 'tmp', 'claims', 'processes'):
            (stage/name).mkdir(mode=0o700)
        try:
            exists = archive.archive_ciphertext_size(home, selected.relative, digest=selected.digest) is not None
            document = (_existing_source(home, selected, source, kind, operation, stage) if exists
                        else _new_source(home, source, requested, kind, task, selected, operation, stage))
            if task is not None and document.get('task') != str(task):
                raise store.WorkerStateError('Original import task differs; ambiguous workspace and full archives retained')
            _finish(home, selected, source, requested, document, operation, stage)
            operation.check()
            selected.bind(stage)
            os.rename(stage, selected.path)
            from hermes_security.browser_worker_pending import sync

            with selected.access(operation=operation) as call:
                check_mapping(selected, requested, kind=kind, task=task)
                sync(selected, call)
            selected.release(operation=operation)
            return {'archive':selected.relative.as_posix(), 'recovery':document['recovery'],
                    'source':str(source), 'workspace':str(selected.tree), 'source_retired':True}
        except (OSError, ValueError, VaultError) as exc:
            raise store.WorkerStateError(
                f'Browser Use import incomplete; keep source {source} and full owned archive {home/selected.relative}. '
                'Close original owners and repeat the same explicit hermes secure-vault repair browser-use selector; '
                f'new or changed survivors are retained: {exc}') from exc
        finally:
            if stage.exists():
                remove_stable_workspace(stage)


def legacy_debt(home):
    """A scanner/repair must not certify raw old Browser Use files as a public cache."""
    from hermes_security import tree_archive

    root = Path(home)/'cache'/'browser-use'
    if not os.path.lexists(root):
        return []
    try:
        for count, (_relative, is_directory) in enumerate(tree_archive._iter_tree(root, archive._policy())):
            if not is_directory or count >= archive.MAX_ENTRIES:
                break
        else:
            return []
    except (OSError, ValueError, VaultError):
        return _legacy_notice(root)
    return _legacy_notice(root)


def _legacy_notice(root):
    return [f'{root}: original Browser Use data retained unmodified; explicitly use hermes secure-vault repair '
            '--browser-use-workspace DIRECTORY --browser-use-task ORIGINAL_TASK_ID. '
            'Ambiguous old task names must be resolved before import.']


def repair_known_default(home):
    """Pathless owner repair admits only the published literal default task root."""
    source = Path(home)/'cache'/'browser-use'/'workspace'/'default'
    if not os.path.lexists(source):
        return {'saved':[], 'retained':[]}
    try:
        result = import_source(home, source, kind='workspace', task='default')
        return {'saved':[result['archive']], 'retained':[]}
    except (OSError, ValueError, VaultError) as exc:
        return {'saved':[], 'retained':[f'{source}: {exc}']}
