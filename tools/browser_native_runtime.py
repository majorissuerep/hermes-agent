"""Native agent-browser owned HOME/config and ephemeral Chromium state.

The selected PM executable stays unchanged. Only this client's environment receives private RAM
HOME/XDG/temp/IPC paths. Its durable arbitrary state uses the WORKER domain; Chromium's default
profile remains ephemeral, with its temporary directory in RAM. Outgoing backends are separate.
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import threading

from hermes_cli import browser_worker_store as store
from hermes_security.session_custody import SessionCustody

_LOCK = threading.Lock()
_SESSIONS = {}
_SOCKETS = {}
_CLEANUP = ContextVar('native_browser_cleanup', default=None)


class NativeEnvironment(dict):
    """Ordinary subprocess strings plus an in-process-only original operation guard."""
    def __init__(self, values, lease, operation, *, directories=None):
        super().__init__(values)
        self.lease, self.operation = lease, operation
        self.directories = directories

    def copy(self):
        return NativeEnvironment(self, self.lease, self.operation, directories=self.directories)

    def check(self):
        self.operation.check()
        self.lease.check()
        if self.directories is None:
            raise store.WorkerStateError('Native browser private directories were not prepared')
        self.directories.check(self)

    @property
    def home(self):
        return self.operation.home

    @property
    def resource(self):
        return self.operation.resource

    @property
    def vault(self):
        return self.operation.vault

    @property
    def authority(self):
        return self.operation.authority

    def derive_key(self, purpose):
        self.check()
        return self.operation.derive_key(purpose)


class NativeLease:
    def __init__(self, home, session, resource):
        self.home, self.session, self.resource = home, session, resource
        self.owner = SessionCustody.capture(home, resource=resource.custody)
        self.socket = resource.path/'r'/hashlib.sha256(session.encode()).hexdigest()[:12]
        self.identity = None
        self.daemon = None
        self.executable = None
        self.original_environment = None
        self.pending = resource.path/'processes'/('unknown-'+hashlib.sha256(session.encode()).hexdigest()[:16])

    def prepare(self):
        with self.resource.access(operation=self.owner):
            self.socket.mkdir(mode=0o700, exist_ok=True)
            self.identity = store._identity(self.socket)
        return str(self.socket)

    def check(self):
        if self.identity is None or store._identity(self.socket) != self.identity:
            raise store.WorkerStateError('The original native browser IPC directory was replaced')


def socket_directory(session):
    from hermes_constants import get_hermes_home

    home = Path(get_hermes_home()).resolve()
    resource = store.resource(home, kind='native-browser-config')
    key = home, session
    with _LOCK:
        lease = _SESSIONS.get(key)
        if lease is not None and lease.resource is not resource:
            raise store.WorkerStateError('This native browser belongs to an earlier unlock; close it before retrying')
    if lease is None:
        selected = NativeLease(home, session, resource)
        selected.prepare()
        with _LOCK:
            lease = _SESSIONS.setdefault(key, selected)
            _SOCKETS[str(lease.socket)] = lease
    lease.check()
    return str(lease.socket)


def environment(values, socket):
    with _LOCK:
        lease = _SOCKETS.get(socket)
    if lease is None:
        raise store.WorkerStateError('Native browser IPC must be allocated in the protected RAM workspace')
    lease.check()
    cleanup = _CLEANUP.get()
    operation = (cleanup[1] if cleanup is not None and cleanup[0] is lease else
                 SessionCustody.capture(lease.home, resource=lease.resource.custody))
    operation.check()
    if cleanup is not None and cleanup[0] is lease and cleanup[2] and lease.original_environment is not None:
        original = lease.original_environment
        result = NativeEnvironment(original, lease, operation, directories=original.directories)
        result.check()
        return result
    # Never silently ignore a pre-existing external credential/config store.
    original = Path(values.get('HOME') or Path.home())/'.agent-browser'
    selected = lease.resource.tree/'home'
    try:
        original.lstat()
        exists = True
    except FileNotFoundError:
        exists = False
    if exists and original.resolve() != (selected/'.agent-browser').resolve():
        raise store.WorkerStateError(
            'Existing native browser state was retained at its selected HOME. Close the browser and '
            'explicitly import it with hermes secure-vault repair --native-browser-state PATH_TO_.agent-browser '
            'before using protected native browsing.')
    profile = values.get('AGENT_BROWSER_PROFILE')
    if profile and not store._prove(Path(profile).expanduser().resolve()).proven:
        raise store.WorkerStateError(
            'AGENT_BROWSER_PROFILE selects persistent storage; its contents were retained. '
            'Use a proven RAM profile or explicitly repair/import that browser profile.')
    with lease.resource.access(operation=operation):
        for directory in (selected, selected/'.config', selected/'.cache', selected/'.local'/'share'):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    result = NativeEnvironment(values, lease, operation)
    result.update(HOME=str(selected), XDG_CONFIG_HOME=str(selected/'.config'),
                  XDG_CACHE_HOME=str(selected/'.cache'), XDG_DATA_HOME=str(selected/'.local'/'share'),
                  TMPDIR=str(lease.resource.path/'tmp'), TMP=str(lease.resource.path/'tmp'),
                  TEMP=str(lease.resource.path/'tmp'), AGENT_BROWSER_SOCKET_DIR=str(lease.socket))
    from tools.browser_native_paths import PrivateDirectories
    result.directories = PrivateDirectories(result)
    if lease.original_environment is None:
        lease.original_environment = result.copy()
    return result


def _record_daemon(env):
    from tools import browser_tool_session as session
    from hermes_platform.browser_process import browser_process_record
    from hermes_platform.processes import process_record_is_dead

    lease = env.lease
    pid = session._read_browser_daemon_pid(str(lease.socket), lease.session)
    record = browser_process_record(pid, executable=lease.executable, socket_dir=lease.socket) if pid is not None else None
    if record is not None:
        lease.daemon = lease.resource.record_process(record)
        lease.pending.unlink(missing_ok=True)
    elif env.close_completed and lease.daemon is not None and process_record_is_dead(lease.daemon):
        lease.pending.unlink(missing_ok=True)


@contextlib.contextmanager
def client_guard(env, argv):
    if not isinstance(env, NativeEnvironment):
        if env.get('AGENT_BROWSER_SOCKET_DIR') in _SOCKETS:
            raise store.WorkerStateError('The native browser operation lost its original session guard')
        yield env
        return
    env.check()
    if env.lease.executable is None:
        env.lease.executable = str(Path(argv[0]).resolve())
    env.close_completed = False
    with env.lease.resource.operation(operation=env) as call:
        store._write(env.lease.pending, {'unknown':True, 'session':env.lease.session,
                                      'executable':env.lease.executable, 'socket':str(env.lease.socket),
                                      'socket_inode':list(env.lease.identity)})
        from hermes_security.browser_worker_pending import sync
        sync(env.lease.resource, call)
        try:
            yield env
        finally:
            _record_daemon(env)


def completed_client(env, argv, returncode, output):
    """A completed fixed JSON close plus kernel death can retire the previously known daemon."""
    from hermes_platform.browser_process import agent_browser_action

    if not isinstance(env, NativeEnvironment) or returncode != 0 or agent_browser_action(argv) != 'close':
        return
    try:
        result = json.loads(output.decode('utf-8-sig'))
    except (ValueError, UnicodeError):
        return
    env.close_completed = isinstance(result, dict) and result.get('success') is True


def _find_lease(session, home=None):
    with _LOCK:
        leases = [lease for (selected, name), lease in _SESSIONS.items()
                  if name == session and (home is None or selected == Path(home).resolve())]
    if not leases:
        return None
    if len(leases) != 1:
        raise store.WorkerStateError('The original native browser owner is ambiguous; RAM state was retained')
    return leases[0]


@contextlib.contextmanager
def cleanup_scope(session, home=None, *, original=False):
    """Capture before any teardown effect; background callbacks retain their original source.

    Explicit later requests may use a new live sibling for the shared profile. Neither path
    allocates a replacement browser, and nested teardown keeps the already-captured operation.
    """
    lease = _find_lease(session, home)
    if lease is None:
        yield
        return
    existing = _CLEANUP.get()
    if existing is not None and existing[0] is lease:
        existing[1].check()
        yield
        return
    operation = lease.owner if original else SessionCustody.capture(lease.home, resource=lease.resource.custody)
    operation.check()
    lease.check()
    token = _CLEANUP.set((lease, operation, original))
    try:
        yield
    finally:
        _CLEANUP.reset(token)


def _cleanup_operation(lease):
    captured = _CLEANUP.get()
    if captured is not None and captured[0] is lease:
        captured[1].check()
        return captured[1]
    return SessionCustody.capture(lease.home, resource=lease.resource.custody)


def captured_socket(session, home=None):
    """Existing lease only: cleanup never allocates another profile or session."""
    lease = _find_lease(session, home)
    if lease is None:
        return None
    _cleanup_operation(lease).check()
    lease.check()
    return str(lease.socket)


def release_session(session, socket):
    """Retire only this already-closed daemon's IPC; shared config remains for its next owner."""
    from hermes_platform.processes import process_record_is_dead
    from hermes_security.protected_workspace import remove_stable_workspace

    with _LOCK:
        lease = _SOCKETS.get(socket)
    if lease is None or lease.session != session:
        raise store.WorkerStateError('The native browser cleanup lost its original resource')
    operation = _cleanup_operation(lease)
    lease.check()
    try:
        lease.pending.lstat()
    except FileNotFoundError:
        pass
    else:
        raise store.WorkerStateError('The native daemon launch identity is unknown; its RAM state was retained')
    if lease.daemon is not None and not process_record_is_dead(lease.daemon):
        raise store.WorkerStateError('The native browser has not verifiably exited; its RAM state was retained')
    remove_stable_workspace(lease.socket)
    with _LOCK:
        _SOCKETS.pop(socket, None)
        _SESSIONS.pop((lease.home, session), None)
        remaining = any(other.resource is lease.resource for other in _SESSIONS.values())
    if not remaining:
        lease.resource.release(operation=operation)


def stop_and_release(session, socket):
    """Original owner only, serialized with clients; never target a recycled or unknown PID."""
    from hermes_platform.browser_process import browser_process_record
    from hermes_platform.processes import process_record_is_dead
    from tools.process_registry import ProcessRegistry

    with _LOCK:
        lease = _SOCKETS.get(socket)
    if lease is None:
        raise store.WorkerStateError('Native browser cleanup has no original lease')
    with store._lock(lease.resource.lock_path):
        _cleanup_operation(lease).check()
        lease.check()
        if lease.daemon is not None and not process_record_is_dead(lease.daemon):
            current = browser_process_record(lease.daemon['pid'], executable=lease.executable, socket_dir=lease.socket)
            if current != lease.daemon:
                raise store.WorkerStateError('The original native browser identity is unreadable or changed')
            ProcessRegistry._terminate_host_pid(current['pid'], current['start_time'])
        release_session(session, socket)
