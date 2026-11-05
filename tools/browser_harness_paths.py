"""Concrete selected Browser Use config/workspace roots, never ambient profile borrowing."""
from __future__ import annotations

import os
from pathlib import Path

from hermes_cli import browser_worker_store as store, browser_use_import as imports
from hermes_security import browser_worker_archive as archive
from hermes_security.session_custody import SessionCustody


def present(path):
    try:
        Path(path).lstat()
    except FileNotFoundError:
        return False
    return True


def has_mapping(home, source, kind):
    selected = imports.mapped_resource(home, source, kind)
    return archive.archive_ciphertext_size(home, selected.relative, digest=selected.digest) is not None


class RootBinding:
    """The selected actual RAM path or a deliberately imported canonical source mapping."""
    def __init__(self, home, requested, *, kind, task=None):
        self.requested, self.source = imports.canonical_source(requested)
        self.kind, self.task, self.ready = kind, task, False
        self.imported = not store._prove(self.source).proven
        if self.imported:
            self.resource = imports.mapped_resource(home, self.source, kind)
            if archive.archive_ciphertext_size(home, self.resource.relative, digest=self.resource.digest) is None:
                flag = '--browser-use-workspace' if kind == 'workspace' else '--browser-use-config'
                raise store.WorkerStateError(
                    f'Selected Browser Use source remains at {self.requested}; explicitly run '
                    f'hermes secure-vault repair {flag} DIRECTORY before protected use. '
                    'Legacy task workspaces also require their exact --browser-use-task.')
        else:
            from hermes_cli.browser_worker_external import existing_browser_use_resource

            self.resource = (existing_browser_use_resource(home, self.source)
                             or store.resource(home, kind='browser-use-explicit-tree', external_tree=self.source))
        self.path = self.resource.tree if self.imported else self.source
        self.operation = SessionCustody.capture(home, resource=self.resource.custody)

    def check(self):
        self.operation.check()
        if self.requested.resolve() != self.source:
            raise store.WorkerStateError('The selected Browser Use source alias changed; every copy retained')
        if self.imported and os.path.lexists(self.source):
            raise store.WorkerStateError('Original Browser Use source reappeared; repeat its explicit offline import before use')
        if self.ready:
            self.resource.check_tree()
            if self.imported:
                imports.check_mapping(self.resource, self.requested, kind=self.kind, task=self.task)

    def materialize(self):
        self.check()
        with self.resource.access(operation=self.operation):
            if not self.imported and self.path != self.resource.tree:
                from hermes_cli.browser_native_state import owned_directory

                with owned_directory(self.resource.tree, self.path.relative_to(self.resource.tree).parts, create=True):
                    self.operation.check()
            if self.imported:
                imports.check_mapping(self.resource, self.requested, kind=self.kind, task=self.task)
            self.ready = True
            self.check()
        return self.path


def workspace(home, task, values):
    selected = values.get('BH_AGENT_WORKSPACE') or os.environ.get('BH_AGENT_WORKSPACE')
    if selected:
        binding = RootBinding(home, selected, kind='workspace')
        return binding.resource, binding
    from tools.browser_use_cli import _TASK_ID_SAFE_RE

    safe = _TASK_ID_SAFE_RE.sub('_', str(task))[:80] or 'default'
    legacy = home/'cache'/'browser-use'/'workspace'/safe
    if present(legacy) or has_mapping(home, legacy, 'workspace'):
        binding = RootBinding(home, legacy, kind='workspace', task=str(task))
        return binding.resource, binding
    return store.resource(home, kind='browser-use-workspace', task=task), None


def _selected_config(values):
    user = Path(values.get('HOME') or Path.home()).expanduser()
    selected_home = values.get('BH_HOME') or values.get('BROWSER_HARNESS_HOME')
    home = (Path(selected_home) if selected_home else Path(values.get('XDG_CONFIG_HOME') or user/'.config')/'browser-harness')
    config = Path(values.get('BH_CONFIG_DIR') or home)
    return home.expanduser().absolute(), config.expanduser().absolute(), bool(selected_home), bool(values.get('BH_CONFIG_DIR'))


class Configuration:
    """Keep distinct HOME and CONFIG selections; one containing root need not be imported twice."""
    def __init__(self, home, values):
        raw_home, raw_config, explicit_home, explicit_config = _selected_config(values)
        home_path, config_path = raw_home.resolve(), raw_config.resolve()
        self.aliases = [(raw_home, home_path), (raw_config, config_path)]
        home_needed = explicit_home or present(raw_home) or has_mapping(home, home_path, 'config')
        self.home_binding = RootBinding(home, raw_home, kind='config') if home_needed else None
        self.config_binding, self.home_relative, self.config_relative = None, Path(), Path()
        if home_path == config_path:
            self.config_binding = self.home_binding
        elif self.home_binding is not None and config_path.is_relative_to(home_path) and not has_mapping(home, config_path, 'config'):
            self.config_binding = self.home_binding
            self.config_relative = config_path.relative_to(home_path)
        elif explicit_config:
            self.config_binding = RootBinding(home, raw_config, kind='config')
        if (self.config_binding is not None and self.home_binding is None
                and home_path.is_relative_to(config_path)):
            self.home_binding = self.config_binding
            self.home_relative = home_path.relative_to(config_path)

    def bindings(self):
        result = []
        for binding in (self.home_binding, self.config_binding):
            if binding is not None and not any(item.resource is binding.resource for item in result):
                result.append(binding)
        return result

    def check(self):
        if any(alias.resolve() != expected for alias, expected in self.aliases):
            raise store.WorkerStateError('Original Browser Use configuration alias changed; every copy retained')
        for binding in self.bindings():
            binding.check()

    def prepare(self, default):
        from hermes_cli.browser_native_state import owned_directory

        self.check()
        if self.home_binding is None:
            with owned_directory(default.tree, ('config',), create=True):
                self.check()
        for binding in self.bindings():
            binding.materialize()
        home = (self.home_binding.path/self.home_relative if self.home_binding else default.tree/'config')
        config = (self.config_binding.path/self.config_relative if self.config_binding else home)
        for binding, relative in ((self.home_binding, self.home_relative), (self.config_binding, self.config_relative)):
            if binding is not None:
                with owned_directory(binding.resource.tree, binding.path.relative_to(binding.resource.tree).parts + relative.parts, create=True):
                    self.check()
        return home, config
