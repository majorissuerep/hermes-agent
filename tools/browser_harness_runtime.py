"""Owned Browser Use files: shared profile config, task workspaces, and private RAM IPC.

Resources are shareable within one original Vault generation. Each request and deferred daemon
cleanup captures its own SessionCustody. Workspace calls serialize only their own task; shared
config locks cover brief admission/checkpoint boundaries, never the whole vendor execution.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
from pathlib import Path
import threading

from hermes_cli import browser_worker_store as store
from hermes_security.session_custody import SessionCustody

_DAEMONS = {}
_LOCK = threading.Lock()


class HarnessEnvironment(dict):
    """Only subprocess strings cross exec; the original operation guard stays in its owner."""
    def __init__(self, values, check, starting=None):
        super().__init__(values)
        self.check = check
        self._starting = starting

    def copy(self):
        return HarnessEnvironment(self, self.check, self._starting)

    def starting(self):
        if self._starting is not None:
            self._starting()



class HarnessRequest:
    def __init__(self, home, task, values):
        self.home = Path(home).resolve()
        # Capture before allocation/routing; later ambient session/home changes cannot renew this call.
        self.admission = SessionCustody.capture(self.home)
        self.config = store.resource(self.home, kind='browser-use-config')
        self.config_operation = SessionCustody.capture(self.home, resource=self.config.custody)
        from tools.browser_harness_paths import Configuration, workspace

        self.workspace, self.workspace_binding = workspace(self.home, str(task or 'default'), values)
        self.configuration = Configuration(self.home, values)
        self.workspace_path = self.workspace_binding.path if self.workspace_binding else self.workspace.tree
        self.workspace_operation = SessionCustody.capture(self.home, resource=self.workspace.custody)
        self.values = dict(values)
        from tools.browser_harness_runtime_paths import RuntimePaths

        self.runtime_paths = RuntimePaths(self.home, self.config, values)
        selected = values.get('BH_AGENT_WORKSPACE') or os.environ.get('BH_AGENT_WORKSPACE')
        self.requested_workspace = Path(selected).expanduser().absolute() if selected else None
        self.workspace_call = None
        self.pinned = {}
        self.daemon = None
        self.name, self.directories = None, None

    def check(self):
        self.admission.check()
        self.config_operation.check()
        self.workspace_operation.check()
        self.configuration.check()
        self.runtime_paths.check()
        if self.workspace_binding is not None:
            self.workspace_binding.check()
        from tools.browser_native_paths import _directory
        if any(_directory(path, strict=True) != identity for path, identity in self.pinned.items()):
            raise store.WorkerStateError('An original Browser Use private directory was replaced')
        if self.workspace_call is not None:
            self.workspace_call.check()

    def configure(self, name):
        self.check()
        name = name or 'default'
        import re

        if re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name) is None:
            raise store.WorkerStateError('Selected Browser Use daemon name is invalid; state retained')
        tag = hashlib.sha256(name.encode()).hexdigest()[:12]
        from hermes_cli.browser_native_state import owned_directory
        from tools.browser_native_paths import _directory

        if self.workspace_binding is not None:
            self.workspace_binding.materialize()
        with self.config.access(operation=self.config_operation):
            self.check()
            config_home, config_dir = self.configuration.prepare(self.config)
            directories = {'home':self.config.tree/'home', 'config':config_dir,
                           'runtime':self.config.path/'r'/tag, 'tmp':self.config.path/'tmp'/tag}
            for path in (directories['home'], directories['runtime'], directories['tmp']):
                self.check()
                with owned_directory(self.config.path, path.relative_to(self.config.path).parts, create=True):
                    _directory(path, strict=True)
            for parts in (('home','.config'),('home','.cache'),('home','.local','share')):
                self.check()
                with owned_directory(self.config.tree, parts, create=True):
                    _directory(self.config.tree.joinpath(*parts), strict=True)
        directories['runtime'] = self.runtime_paths.prepare('BH_RUNTIME_DIR', directories['runtime'], self.check)
        directories['tmp'] = self.runtime_paths.prepare('BH_TMP_DIR', directories['tmp'], self.check)
        env = HarnessEnvironment(self.values, self.child_check, self.starting)
        env.update(BU_NAME=name, HOME=str(directories['home']), HERMES_HOME=str(self.home), BH_HOME=str(config_home),
                   BROWSER_HARNESS_HOME=str(config_home), BH_CONFIG_DIR=str(directories['config']),
                   BH_AGENT_WORKSPACE=str(self.workspace_path), BH_RUNTIME_DIR=str(directories['runtime']),
                   BH_TMP_DIR=str(directories['tmp']),
                   BH_RUNTIME_DIR_SHARED=self.runtime_paths.shared('BH_RUNTIME_DIR_SHARED'),
                   BH_TMP_DIR_SHARED=self.runtime_paths.shared('BH_TMP_DIR_SHARED'),
                   TMPDIR=str(directories['tmp']), TMP=str(directories['tmp']), TEMP=str(directories['tmp']),
                   XDG_CONFIG_HOME=str(directories['home']/'.config'),
                   XDG_CACHE_HOME=str(directories['home']/'.cache'),
                   XDG_DATA_HOME=str(directories['home']/'.local'/'share'))
        self.name, self.directories = name, directories
        self.values = env
        self.pinned = {Path(env[key]):_directory(Path(env[key]), strict=True) for key in (
            'HOME','BH_HOME','BH_CONFIG_DIR','BH_RUNTIME_DIR','BH_TMP_DIR','TMPDIR',
            'XDG_CONFIG_HOME','XDG_CACHE_HOME','XDG_DATA_HOME')}
        if self.workspace_binding is not None:
            self.pinned[self.workspace_path] = _directory(self.workspace_path, strict=True)
        return env

    def child_check(self):
        self.check()
        if self.workspace_call is None:
            raise store.WorkerStateError('Browser Use child has no active original workspace operation')
        with _LOCK:
            if _DAEMONS.get((self.home, self.name)) is not self.daemon:
                raise store.WorkerStateError('Original named Browser Use operation was closed; no replacement callback launched')

    def starting(self):
        from hermes_security.browser_worker_pending import sync

        self.child_check()
        with self.config.operation(operation=self.config_operation) as call:
            self.child_check()
            self.daemon.require_target()
            self.daemon.mark_unknown()
            sync(self.config, call)
            self.daemon.mark_workspace(self.workspace, self.workspace_operation)
            sync(self.workspace, self.workspace_call)
            for binding in self.configuration.bindings():
                with binding.resource.operation(operation=binding.operation) as selected:
                    self.child_check()
                    self.daemon.mark_workspace(binding.resource, binding.operation)
                    sync(binding.resource, selected)
        self.child_check()

    @contextlib.contextmanager
    def execution(self):
        with self.workspace.operation(operation=self.workspace_operation) as workspace_call:
            self.workspace_call = workspace_call
            self.check()
            with _LOCK:
                daemon = _DAEMONS.get((self.home, self.name))
                if daemon is not None and (daemon.resource is not self.config or not daemon.matches(self.values)):
                    raise store.WorkerStateError('Existing named Browser Use target differs or belongs to an earlier unlock; retain it and close its original owner before reuse')
                if daemon is None:
                    daemon = _DAEMONS.setdefault((self.home, self.name), HarnessDaemon(self.name, self, self.directories))
            self.daemon = daemon
            try:
                yield self
            finally:
                try:
                    self.check()
                    self.daemon.observe(self.workspace)
                    with self.config.operation(operation=self.config_operation):
                        self.check()
                    for binding in self.configuration.bindings():
                        with binding.resource.operation(operation=binding.operation):
                            self.check()
                finally:
                    self.workspace_call = None


class HarnessDaemon:
    def __init__(self, name, request, directories):
        self.name, self.resource = name, request.config
        self.owner = request.config_operation
        self.environment = dict(request.values)
        self.runtime, self.tmp = directories['runtime'], directories['tmp']
        self.inodes = store._identity(self.runtime), store._identity(self.tmp)
        self.record = None
        self.attempted = False
        self.workspaces = []
        self.pending_name = 'unknown-harness-'+hashlib.sha256(name.encode()).hexdigest()[:16]

    def matches(self, values):
        selected = ('HOME','BH_HOME','BH_CONFIG_DIR','BH_RUNTIME_DIR','BH_TMP_DIR',
                    'BH_RUNTIME_DIR_SHARED','BH_TMP_DIR_SHARED','BU_CDP_URL','BU_CDP_WS')
        return all(self.environment.get(key) == values.get(key) for key in selected)

    @property
    def pid_path(self):
        stem = 'bu-'+self.name if self.environment.get('BH_RUNTIME_DIR_SHARED') == '1' else 'bu'
        return self.runtime/(stem+'.pid')

    def require_target(self):
        from hermes_platform.browser_process import harness_process_record, harness_runtime_absent

        self.check()
        record = harness_process_record(self.pid_path, runtime=self.runtime, name=self.name, expected_env=self.environment)
        if record is None and not harness_runtime_absent(self.pid_path, runtime=self.runtime, name=self.name):
            raise store.WorkerStateError('Existing Browser Use daemon identity is unknown; original runtime was retained')

    def check(self):
        if (store._identity(self.runtime), store._identity(self.tmp)) != self.inodes:
            raise store.WorkerStateError('The original Browser Use runtime was replaced; every copy was retained')

    def mark_unknown(self):
        self.check()
        self.attempted = True
        from sys import executable

        store._write(self.resource.path/'processes'/self.pending_name,
                     {'unknown':True, 'kind':'browser-use-harness', 'session':self.name,
                      'shared':self.environment.get('BH_RUNTIME_DIR_SHARED') == '1',
                      'executable':str(Path(executable).resolve()),
                      'socket':str(self.runtime), 'socket_inode':list(self.inodes[0])})

    def mark_workspace(self, selected, operation):
        if not any(resource is selected for resource, _source in self.workspaces):
            self.workspaces.append((selected, operation))
        store._write(selected.path/'processes'/self.pending_name,
                     store._read(self.resource.path/'processes'/self.pending_name))

    def observe(self, workspace=None):
        from hermes_platform.browser_process import harness_process_record

        self.check()
        record = harness_process_record(self.pid_path, runtime=self.runtime, name=self.name, expected_env=self.environment)
        if record is None:
            from hermes_platform.browser_process import harness_runtime_absent

            if harness_runtime_absent(self.pid_path, runtime=self.runtime, name=self.name):
                (self.resource.path/'processes'/self.pending_name).unlink(missing_ok=True)
                for selected, _operation in self.workspaces:
                    (selected.path/'processes'/self.pending_name).unlink(missing_ok=True)
                self.attempted = False
            return
        self.record = self.resource.record_process(record)
        (self.resource.path/'processes'/self.pending_name).unlink(missing_ok=True)
        if workspace is not None:
            workspace.record_process(record)
            (workspace.path/'processes'/self.pending_name).unlink(missing_ok=True)

    def stop(self, cmd, run_cli, *, original):
        from hermes_platform.browser_process import harness_process_record
        from hermes_platform.processes import process_record_is_dead

        operation = self.owner if original else SessionCustody.capture(self.resource.home, resource=self.resource.custody)
        operation.check()
        self.check()
        self.observe()
        if self.record is None and self.attempted:
            raise store.WorkerStateError(
                f'Browser Use daemon identity is unknown; state retained at {self.resource.path}. '
                'Close its original owners and run hermes secure-vault repair.')
        if self.record is not None and not process_record_is_dead(self.record):
            if harness_process_record(self.pid_path, runtime=self.runtime, name=self.name, expected_env=self.environment) != self.record:
                raise store.WorkerStateError('The original Browser Use daemon changed; no stop command was sent')
            operation.check()
            result = run_cli([*cmd, '--reload'], '', dict(self.environment), 15)
            if result.returncode != 0 or not process_record_is_dead(self.record):
                raise store.WorkerStateError('Browser Use daemon did not verifiably exit; original RAM and archive retained')
        with self.resource.operation(operation=operation):
            (self.resource.path/'processes'/self.pending_name).unlink(missing_ok=True)
        for selected, source in self.workspaces:
            fresh = source if original else SessionCustody.capture(selected.home, resource=selected.custody)
            with selected.operation(operation=fresh):
                (selected.path/'processes'/self.pending_name).unlink(missing_ok=True)
            selected.release(operation=fresh)
        with _LOCK:
            if _DAEMONS.get((self.resource.home, self.name)) is self:
                del _DAEMONS[self.resource.home, self.name]
            remaining = any(other.resource is self.resource for other in _DAEMONS.values())
        if not remaining:
            self.resource.release(operation=operation)


def captured_daemons():
    with _LOCK:
        return list(_DAEMONS.values())
