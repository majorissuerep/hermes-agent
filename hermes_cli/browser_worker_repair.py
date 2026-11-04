"""Deliberate native browser-state import and quiescent worker RAM recovery.

The selected legacy source is snapshot with the same WORKER codec, restored and compared in
proven RAM, and sealed at its new owned location before its original directory can be retired.
The encrypted original-source snapshot is retained for recovery. Nothing imports ambient auth.
"""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import shutil
import stat

from hermes_cli import browser_worker_store as store
from hermes_security import browser_worker_archive as archive
from hermes_security.errors import VaultError
from hermes_security.session_custody import SessionCustody


def _source(home, selected):
    from hermes_security.io import _home_for

    source = Path(selected).expanduser().resolve(strict=True)
    if (source.name != '.agent-browser' or source == home or home.is_relative_to(source)
            or (source/'.hermes-vault').exists()):
        raise store.WorkerStateError('Select the original .agent-browser state directory, never an encrypted home')
    owner = _home_for(source/'auth.json')
    if owner is not None and owner != home:
        raise store.WorkerStateError('The selected native browser state belongs to another encrypted profile')
    info = source.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise store.WorkerStateError('The selected native browser state is not an owned directory')
    return source, (info.st_dev, info.st_ino)


def _source_idle(source):
    from hermes_platform.processes import pid_exists

    for path in source.rglob('*.pid'):
        if path.is_symlink():
            raise store.WorkerStateError('Native browser PID metadata is linked; the source was retained')
        try:
            pid = int(path.read_text())
        except (OSError, ValueError) as exc:
            raise store.WorkerStateError('Native browser PID identity is unknown; the source was retained') from exc
        if pid <= 0 or pid_exists(pid):
            raise store.WorkerStateError('Close the native browser before importing its state')


def _stage(selected):
    from hermes_security.protected_workspace import fresh_sibling_workspace

    stage = fresh_sibling_workspace(selected.path, 'import', prove=store._prove)
    for name in ('tree', 'r', 'tmp', 'claims', 'processes'):
        (stage/name).mkdir(mode=0o700)
    (stage/'tree'/'home').mkdir(mode=0o700)
    target = stage/'tree'/'home'/'.agent-browser'
    target.mkdir(mode=0o700)
    return stage, target


def import_native_home(home, source):
    """Explicit move into encrypted owned state; keep an encrypted source snapshot permanently."""
    from hermes_security.protected_workspace import remove_stable_workspace

    home = Path(home).resolve()
    source, original_inode = _source(home, source)
    selected = store.resource(home, kind='native-browser-config')
    operation = SessionCustody.capture(home, resource=selected.custody)
    recovery_identity = {'kind':'native-browser-import-recovery', 'source':str(source), 'nonce':secrets.token_hex(16)}
    recovery_digest = archive.identity_digest(operation, recovery_identity)
    recovery = archive.archive_relative(recovery_digest)
    with store._lock(selected.lock_path):
        if (home/selected.relative).exists():
            raise store.WorkerStateError('Native browser state already has an archive; both copies were retained for explicit merge')
        if os.path.lexists(selected.path):
            selected._validate()
            store._require_idle(selected)
            if any(selected.tree.iterdir()):
                raise store.WorkerStateError('Native browser state already exists in RAM; both copies were retained')
        _source_idle(source)
        expected = archive.write_archive(home, recovery, source, custody=operation,
                                         identity=recovery_identity, digest=recovery_digest)
        stage, target = _stage(selected)
        try:
            archive.replay_archive(home, recovery, custody=operation, digest=recovery_digest, target=target)
            if archive.tree_digest(target) != expected or archive.tree_digest(source) != expected:
                raise store.WorkerStateError('Native browser source changed during import; source and recovery archive were retained')
            store._write(stage/'tree'/'.native-home-import.json',
                         {'source':str(source), 'source_inode':list(original_inode), 'recovery':recovery.as_posix()})
            archive.write_archive(home, selected.relative, stage/'tree', custody=operation,
                                  identity=selected.identity, digest=selected.digest)
            _source_idle(source)
            current = source.lstat()
            if (current.st_dev, current.st_ino) != original_inode or archive.tree_digest(source) != expected:
                raise store.WorkerStateError('Native browser source changed; verified archives and original were retained')
            operation.check()
            if os.path.lexists(selected.path):
                store._require_idle(selected)
                remove_stable_workspace(selected.path)
            selected.bind(stage)
            os.rename(stage, selected.path)
            operation.check()
            shutil.rmtree(source)
        except (OSError, ValueError, VaultError) as exc:
            raise store.WorkerStateError(
                f'Native browser import is incomplete; original data at {source} and encrypted recovery '
                f'{home/recovery} were retained. Keep both and inspect hermes secure-vault repair '
                '--native-browser-state before removing either copy.') from exc
        finally:
            if stage.exists():
                remove_stable_workspace(stage)
    return {'archive':selected.relative.as_posix(), 'recovery':recovery.as_posix(), 'source_retired':True}
