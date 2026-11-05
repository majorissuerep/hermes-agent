"""Shared Lightpanda WORKER cache and per-operation private RAM launch leases.

A running SQLite/WAL cache is never archived. The last verified checkpoint and typed
pending reference remain until every producer has verifiably exited.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import secrets

from hermes_cli import browser_worker_store as store
from hermes_security import browser_worker_archive as archive, browser_worker_pending as pending
from hermes_security.operation_custody import guarded
from hermes_security.session_custody import SessionCustody

KIND = 'lightpanda-http-cache'


def vendor_environment(values):
    from tools.environments.local_env_policy import strip_local_vault_authority

    env = strip_local_vault_authority(dict(values))
    env.setdefault('LIGHTPANDA_DISABLE_TELEMETRY', 'true')
    return env


def legacy_path(home):
    return Path(home)/'cache'/'browser-use'/'lightpanda'


def require_no_legacy(home):
    source = legacy_path(home)
    if os.path.lexists(source):
        raise store.WorkerStateError(
            f'Original Lightpanda state retained at {source}; explicitly run '
            'hermes manage apply browser.worker.repair before protected use. No old control file was adopted.')


def _directory(root, relative):
    from hermes_cli.browser_native_state import owned_directory

    with owned_directory(root, Path(relative).parts, create=True):
        return store._identity(root/relative)


def _processes(resource, operation):
    for path in sorted((resource.path/'processes').iterdir()):
        operation.check()
        yield path, store._read(path)


def quiescent(resource, operation):
    from hermes_platform.processes import process_record_is_dead
    from hermes_platform.lightpanda_process import require_cache_idle

    active = False
    for _path, record in _processes(resource, operation):
        if record.get('unknown') is True or record.get('kind') not in {'lightpanda','lightpanda-supervisor'}:
            raise store.WorkerStateError('A Lightpanda producer identity is unknown; RAM and prior archive retained')
        if not process_record_is_dead(record):
            active = True
    if active:
        return False
    for path in (resource.path/'claims').iterdir():
        operation.check()
        record = store._read(path)
        if record.get('pid') != os.getpid() and not process_record_is_dead(record):
            raise store.WorkerStateError('Another Lightpanda owner remains live or unknown; shared state retained')
    require_cache_idle(resource.tree/'http-cache', check_alive=operation.check)
    return True


class LightpandaLease:
    def __init__(self, home, session):
        if re.fullmatch(r'[A-Za-z0-9_-]{1,128}', session) is None:
            raise store.WorkerStateError('Lightpanda session name is invalid')
        from agent.session_context import get_session_env

        self.home, self.session = Path(home).resolve(), session
        self.source = get_session_env('HERMES_SESSION_ID', '')
        admission = guarded(SessionCustody.capture(self.home))
        require_no_legacy(self.home)
        self.resource = store.resource(self.home, kind=KIND)
        from tools.browser_lightpanda import check_resource_epoch

        check_resource_epoch(self.resource)
        self.source_operation = SessionCustody.capture(self.home, resource=self.resource.custody)
        self.operation = guarded(self.source_operation)
        admission.check()
        self.token = secrets.token_hex(16)
        tag = hashlib.sha256(session.encode()).hexdigest()[:16]+'-'+self.token
        self.runtime = self.resource.path/'r'/tag
        self.temporary = self.resource.path/'tmp'/tag
        self.private_home = self.resource.tree/'sessions'/tag/'home'
        self.log = self.resource.tree/'logs'/(tag+'.log')
        self.cache = self.resource.tree/'http-cache'
        self.unknown = self.resource.path/'processes'/('unknown-lightpanda-'+self.token)
        self.directories = {}
        self.supervisor_record = None
        self.record = None
        self.child = None

    def check(self, operation=None):
        (self.operation if operation is None else operation).check()
        self.resource._validate()
        if any(store._identity(path) != identity or not store._prove(path).proven
               for path, identity in self.directories.items()):
            raise store.WorkerStateError('Original Lightpanda RAM directory was replaced; every copy retained')

    @contextmanager
    def access(self, operation=None):
        selected = self.operation if operation is None else operation
        with self.resource.access(operation=selected) as call:
            self.check(selected)
            yield call

    def prepare(self, call):
        self.check(call)
        for path in (self.cache,self.log.parent,self.runtime,self.temporary,self.private_home,
                     self.private_home/'.config',self.private_home/'.cache',self.private_home/'.local'/'share'):
            _directory(self.resource.path, path.relative_to(self.resource.path))
            self.directories[path] = store._identity(path)
        if archive.archive_ciphertext_size(self.home, self.resource.relative, digest=self.resource.digest) is None:
            if not quiescent(self.resource, call):
                raise store.WorkerStateError('An active cache has no verified checkpoint; no new vendor launched')
            call.checkpoint()
        pending.sync(self.resource, call)

    def environment(self, values):
        self.check()
        env = dict(values)
        env.update(HOME=str(self.private_home), TMPDIR=str(self.temporary), TMP=str(self.temporary),
                   TEMP=str(self.temporary), XDG_CONFIG_HOME=str(self.private_home/'.config'),
                   XDG_CACHE_HOME=str(self.private_home/'.cache'),
                   XDG_DATA_HOME=str(self.private_home/'.local'/'share'))
        return vendor_environment(env)

    def starting(self, binary, call):
        self.check(call)
        store._write(self.unknown, {'unknown':True, 'kind':'lightpanda', 'session':self.session,
                                  'executable':str(binary), 'socket':str(self.runtime),
                                  'socket_inode':list(store._identity(self.runtime))})
        pending.sync(self.resource, call)

    def identify(self, child, record, call):
        from hermes_platform.processes import get_process_start_time

        self.check(call)
        started = get_process_start_time(child.pid)
        if started is None:
            raise store.WorkerStateError('Lightpanda supervisor identity is unavailable; RAM retained')
        self.child, self.record = child, {**record, 'kind':'lightpanda'}
        self.supervisor_record = {'pid':child.pid,'start_time':started,'kind':'lightpanda-supervisor'}
        self.resource.record_process(self.record)
        if child.pid != record['pid']:
            self.resource.record_process(self.supervisor_record)
        self.unknown.unlink(missing_ok=True)
        pending.sync(self.resource, call)

    def finish(self, operation):
        """Called only after the exact owned supervisor has reaped its complete child tree."""
        with self.access(operation) as call:
            records = {record['pid']:record for record in (self.supervisor_record,self.record) if record is not None}
            for pid, record in records.items():
                path = self.resource.path/'processes'/str(pid)
                try:
                    current = store._read(path)
                except FileNotFoundError:
                    current = record  # An earlier confirmed close may have reached this cleanup already.
                if current != record:
                    raise store.WorkerStateError('Original Lightpanda process metadata was replaced; state retained')
                path.unlink(missing_ok=True)
            self.unknown.unlink(missing_ok=True)
            if quiescent(self.resource, call):
                call.checkpoint()
            pending.sync(self.resource, call)
        return self.resource.relative.as_posix()


def checkpoint_idle(home, operation):
    """Fresh explicit worker repair may seal an idle cache without borrowing an earlier request."""
    resource = store.resource(home, kind=KIND)
    if not resource.path.exists():
        return None
    operation = guarded(SessionCustody.capture(home, resource=resource.custody))
    with resource.access(operation=operation) as call:
        if not quiescent(resource, call):
            return None
        for path, record in list(_processes(resource, call)):
            from hermes_platform.processes import process_record_is_dead
            if not process_record_is_dead(record):
                raise store.WorkerStateError('A Lightpanda producer remains live or unknown')
            path.unlink()
        call.checkpoint()
        pending.sync(resource, call)
    resource.release(operation=operation)
    return resource.relative.as_posix()
