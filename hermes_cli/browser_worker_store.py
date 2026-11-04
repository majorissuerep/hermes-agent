"""Shared private browser resources: active files in RAM, durable WORKER archives.

A resource owns one home/Vault generation and state identity. Each use captures its own
SessionCustody; a sibling can use shared configuration without renewing an older callback.
Only ``tree`` is persisted. IPC, logs and temporary browser profiles stay in separate RAM
children. Failed checkpoints retain the active tree and previous archive.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
import time

from hermes_security import browser_worker_archive as archive
from hermes_security.errors import VaultError
from hermes_security.session_custody import SessionCustody

PREFIX = 'hermes-browser-worker-'
_BINDING = '.worker-binding.json'
_POOL = {}
_POOL_LOCK = threading.Lock()
_LOCKS = {}
_HELD = threading.local()


class WorkerStateError(VaultError):
    """Private browser state was retained because it could not be used safely."""


def _prove(path):
    from hermes_platform.memory_protection import ram_only_directory_proof

    return ram_only_directory_proof(path)


def _capacity(path, *, proof):
    from hermes_platform.memory_capacity import capacity_budget

    return capacity_budget(path, proof=proof)


def _identity(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise WorkerStateError('Private browser RAM state is not an owned 0700 directory')
    return info.st_dev, info.st_ino


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 65536):
            raise WorkerStateError('Private browser RAM binding is not an owned bounded regular file')
        document = json.load(stream)
    if not isinstance(document, dict):
        raise WorkerStateError('Private browser RAM binding is unreadable')
    return document


def _write(path, document):
    from hermes_security.vault import _atomic_write

    _atomic_write(path, json.dumps(document, sort_keys=True).encode())


@contextlib.contextmanager
def _lock(path, *, wait=60):
    from hermes_platform.processes import release_file_lock, try_acquire_file_lock

    with _POOL_LOCK:
        local = _LOCKS.setdefault(path, threading.RLock())
    with local:
        held = getattr(_HELD, 'paths', None)
        if held is None:
            held = _HELD.paths = set()
        if path in held:
            yield
            return
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, 'r+b') as handle:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise WorkerStateError('Private browser state lock has unsafe ownership or permissions')
            deadline = time.monotonic() + wait
            while not try_acquire_file_lock(handle):
                if time.monotonic() >= deadline:
                    raise WorkerStateError('Private browser state is busy in another request; no files were replaced')
                time.sleep(0.05)
            try:
                held.add(path)
                yield
            finally:
                held.remove(path)
                release_file_lock(handle)


class WorkerCall:
    """Original operation guard plus pinned RAM inodes, compatible with atomic encrypted writers."""
    def __init__(self, resource, operation):
        self.resource, self.operation = resource, operation
        self.directories = {name: _identity(resource.path/name) for name in ('tree', 'r', 'tmp', 'claims', 'processes')}
        self.root_identity = _identity(resource.path)

    @property
    def vault(self):
        return self.operation.vault

    @property
    def authority(self):
        return self.operation.authority

    def check(self):
        self.operation.check()
        if (not _prove(self.resource.path).proven or _identity(self.resource.path) != self.root_identity
                or any(_identity(self.resource.path/name) != expected for name, expected in self.directories.items())):
            raise WorkerStateError('Private browser RAM state was replaced; every copy was retained')

    def derive_key(self, purpose):
        self.check()
        return self.operation.derive_key(purpose)

    def checkpoint(self):
        self.check()
        return archive.write_archive(self.resource.home, self.resource.relative, self.resource.tree,
                                     custody=self, identity=self.resource.identity, digest=self.resource.digest)


class WorkerResource:
    """One shared config tree or per-task workspace; never globally pinned to a first local session."""
    def __init__(self, home, identity, custody, *, base=None):
        from hermes_security.protected_workspace import protected_workspace_base, stable_workspace_path

        self.home, self.identity, self.custody = home, identity, custody
        self.digest = archive.identity_digest(custody, identity)
        self.relative = archive.archive_relative(self.digest)
        self.base = Path(protected_workspace_base() if base is None else base).resolve()
        if not _prove(self.base).proven:
            raise WorkerStateError('Private browser state requires a proven RAM-only protected workspace')
        namespace = hashlib.sha256((str(home)+'|'+self.digest).encode()).hexdigest()[:16]
        self.path = stable_workspace_path(PREFIX+namespace, self.base)
        self.lock_path = self.base/(self.path.name+'.lock')
        self.tree = self.path/'tree'

    def _validate(self):
        document = _read(self.path/_BINDING)
        if (document.get('version') != 1 or document.get('home') != str(self.home)
                or document.get('identity') != self.digest or document.get('identity_doc') != self.identity):
            raise WorkerStateError('Private browser RAM binding belongs to another resource')
        for name, expected in document.get('inodes', {}).items():
            if name not in ('', 'tree', 'r', 'tmp', 'claims', 'processes') or list(_identity(self.path/name)) != expected:
                raise WorkerStateError('Private browser RAM directory was replaced; all copies were retained')
        if set(document.get('inodes', {})) != {'', 'tree', 'r', 'tmp', 'claims', 'processes'}:
            raise WorkerStateError('Private browser RAM binding is incomplete')

    def bind(self, stage):
        binding = {'version':1, 'home':str(self.home), 'identity':self.digest, 'identity_doc':self.identity,
                   'inodes':{name:list(_identity(stage/name)) for name in ('', 'tree', 'r', 'tmp', 'claims', 'processes')}}
        _write(stage/_BINDING, binding)

    def _materialize(self, operation):
        from hermes_security.protected_workspace import fresh_sibling_workspace, remove_stable_workspace

        operation.check()
        if os.path.lexists(self.path):
            self._validate()
            return
        stage = fresh_sibling_workspace(self.path, 'allocate', prove=_prove)
        try:
            for name in ('tree', 'r', 'tmp', 'claims', 'processes'):
                (stage/name).mkdir(mode=0o700)
            size = archive.archive_ciphertext_size(self.home, self.relative, digest=self.digest)
            if size is not None:
                budget = _capacity(self.base, proof=_prove(self.base))
                if budget.available_bytes is None or budget.available_bytes < size + 64*1024**2:
                    raise WorkerStateError('Insufficient proven RAM budget; encrypted browser state was left unchanged')
                archive.replay_archive(self.home, self.relative, custody=operation, digest=self.digest, target=stage/'tree')
            self.bind(stage)
            operation.check()
            os.rename(stage, self.path)
        except BaseException:
            remove_stable_workspace(stage)
            raise
        self._validate()

    @contextlib.contextmanager
    def access(self, *, operation=None):
        from hermes_security import browser_worker_pending as pending

        operation = operation or SessionCustody.capture(self.home, resource=self.custody)
        if operation.home != self.home or operation.resource is not self.custody:
            raise WorkerStateError('The browser operation belongs to another resource')
        with _lock(self.lock_path):
            document = pending.read_optional(self.home, self.digest, custody=operation)
            if document is not None:
                retained = Path(document['path'])
                if retained != self.path or pending.root_missing(document) or list(_identity(retained)) != document['root_inode']:
                    raise WorkerStateError(
                        f'Original private browser RAM state is retained or missing at {retained}; '
                        f'last checkpoint {self.home/self.relative} was not replaced. Close its owners and run '
                        'hermes secure-vault repair to inspect that exact reference before starting another copy.')
            self._materialize(operation)
            call = WorkerCall(self, operation)
            call.check()
            self.claim()
            yield call

    @contextlib.contextmanager
    def operation(self, *, operation=None):
        from hermes_security import browser_worker_pending as pending

        with self.access(operation=operation) as call:
            pending.sync(self, call)
            # Establish recoverable typed state before any external producer can write.
            # A failed first checkpoint never launches that producer.
            if archive.archive_ciphertext_size(self.home, self.relative, digest=self.digest) is None:
                call.checkpoint()
            try:
                yield call
            finally:
                try:
                    call.checkpoint()
                    pending.sync(self, call)
                except (VaultError, OSError, ValueError) as exc:
                    raise WorkerStateError(
                        'Private browser state could not be saved; the original archive and RAM files were retained. '
                        'Close the browser and use hermes secure-vault repair before removing the RAM workspace.') from exc

    def claim(self):
        from hermes_platform.processes import get_process_start_time

        started = get_process_start_time(os.getpid())
        if started is None:
            raise WorkerStateError('Cannot establish the private browser state owner identity')
        _write(self.path/'claims'/str(os.getpid()), {'pid':os.getpid(), 'start_time':started})

    def release(self, *, operation=None):
        from hermes_security import browser_worker_pending as pending

        operation = operation or SessionCustody.capture(self.home, resource=self.custody)
        with _lock(self.lock_path):
            operation.check()
            self._validate()
            (self.path/'claims'/str(os.getpid())).unlink(missing_ok=True)
            if pending.read_optional(self.home, self.digest, custody=operation) is not None:
                pending.sync(self, WorkerCall(self, operation))

    def record_process(self, record):
        _write(self.path/'processes'/str(record['pid']), record)
        return record

    @staticmethod
    def read_record(path):
        return _read(path)


def resource(home, *, kind, task=None):
    """Shared per-home config or explicit per-home+task workspace, under the original Vault epoch."""
    home = Path(home).expanduser().resolve()
    custody = archive.capture_custody(home)
    identity = {'kind':kind, **({'task':str(task)} if task is not None else {})}
    key = home, json.dumps(identity, sort_keys=True)
    with _POOL_LOCK:
        current = _POOL.get(key)
    if (current is not None and current.custody.vault is custody.vault
            and (current.custody.authority is None or current.custody.authority.is_current())):
        current.custody.check()
        return current
    selected = WorkerResource(home, identity, custody)
    with _POOL_LOCK:
        _POOL[key] = selected
    return selected


def resources_in_ram(home):
    """Only authenticated bindings for this exact selected home, never another profile's state."""
    from hermes_security.protected_workspace import protected_workspace_base
    from hermes_security import browser_worker_pending as pending

    home = Path(home).resolve()
    seen = set()
    custody = archive.capture_custody(home)
    for reference in pending.paths(home):
        document = pending.read(home, reference.relative_to(home), custody=custody)
        retained = Path(document['path'])
        if pending.root_missing(document):
            raise WorkerStateError(
                f'Private browser RAM copy is missing at {retained}; reference {reference} and last '
                f'checkpoint {home/archive.archive_relative(document["digest"])} were retained. '
                'Run hermes secure-vault repair to inspect the original owners and checkpoint.')
        selected = WorkerResource(home, document['identity'], custody, base=retained.parent)
        if selected.path != retained or list(_identity(retained)) != document['root_inode']:
            raise WorkerStateError(f'Private browser RAM copy at {retained} was replaced; all copies were retained')
        selected._validate()
        seen.add(retained)
        yield selected
    try:
        base = Path(protected_workspace_base()).resolve()
    except (VaultError, OSError):
        if seen:
            return
        raise
    if not _prove(base).proven:
        if seen:
            return
        raise WorkerStateError('Cannot inspect private browser state without its proven RAM workspace')
    for path in sorted(base.glob(PREFIX+'*')):
        if path.name.endswith('.lock'):
            continue
        if path in seen:
            continue
        document = _read(path/_BINDING)
        if document.get('home') != str(home):
            continue
        identity = document.get('identity_doc')
        if not isinstance(identity, dict):
            raise WorkerStateError('Private browser RAM state lacks its original identity')
        selected = WorkerResource(home, identity, archive.capture_custody(home))
        if selected.path != path:
            raise WorkerStateError('Private browser RAM state has an invalid resource name')
        selected._validate()
        yield selected


def _require_idle(selected):
    from hermes_platform.processes import process_record_is_dead

    for folder in ('claims', 'processes'):
        for entry in (selected.path/folder).iterdir():
            record = _read(entry)
            if entry.name != str(record.get('pid')) or not process_record_is_dead(record):
                raise WorkerStateError('A private browser owner is live or unknown; close every browser and Hermes first')


def retire_quiescent(home):
    """Explicit offline repair: checkpoint before retiring any sole RAM copy; never signal an owner."""
    from hermes_security.protected_workspace import remove_stable_workspace
    from hermes_security import browser_worker_pending as pending

    operation = SessionCustody.capture(home)
    missing, retained = pending.recover_missing(home, custody=operation)
    saved = []
    if retained:
        return {'archived':saved, 'retained':retained, 'missing':missing}
    try:
        candidates = list(resources_in_ram(home))
    except (VaultError, OSError):
        if not archive.archive_paths(home) and not pending.paths(home):
            return {'archived':[], 'retained':[], 'missing':missing}  # no admitted private worker state
        raise
    for selected in candidates:
        try:
            with _lock(selected.lock_path):
                operation = SessionCustody.capture(selected.home, resource=selected.custody)
                selected._validate()
                _require_idle(selected)
                call = WorkerCall(selected, operation)
                call.checkpoint()
                _require_idle(selected)
                call.check()
                remove_stable_workspace(selected.path)
                if pending.read_optional(home, selected.digest, custody=operation) is not None:
                    pending.remove(home, selected.digest, custody=operation)
                saved.append(selected.relative.as_posix())
        except (VaultError, OSError, ValueError) as exc:
            retained.append(f'{selected.relative.as_posix()} ({exc})')
    return {'archived':saved, 'retained':retained, 'missing':missing}
