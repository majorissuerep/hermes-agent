"""An explicitly selected private RAM workspace keeps its actual path and complete file tree.

The external tree is never a symlink into a hidden alternative. Its original inode and owned
resource marker are checked through each call. Only explicit quiescent repair may retire it,
after authenticating a full checkpoint. SSD sources require a separate deliberate import.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from hermes_cli import browser_worker_store as store
from hermes_security import browser_worker_archive as archive

_MARKER = '.hermes-browser-worker-owner.json'


def external_identity(home, requested):
    requested = Path(requested).expanduser().absolute()
    selected = requested.resolve()
    if selected == home or selected.is_relative_to(home) or home.is_relative_to(selected):
        raise store.WorkerStateError('An explicit browser workspace cannot be an encrypted home or its state tree')
    from hermes_security.io import _home_for
    from hermes_security.profile_binding import for_file

    probe = selected/'browser-workspace-scope-probe'
    owner = for_file(probe).home if _home_for(probe) is not None else None
    if owner is not None and owner != home:
        raise store.WorkerStateError('The explicit browser path belongs to another encrypted profile')
    if any(parent.name.startswith(store.PREFIX) for parent in [selected, *selected.parents]):
        raise store.WorkerStateError('An explicit browser workspace cannot borrow another owned worker resource')
    from hermes_security.protected_workspace import protected_workspace_base

    base = Path(protected_workspace_base()).resolve()
    if selected == base or base.is_relative_to(selected):
        raise store.WorkerStateError('An explicit workspace cannot contain the protected workspace base')
    if any(os.path.lexists(parent/_MARKER) for parent in selected.parents):
        raise store.WorkerStateError('An explicit workspace cannot borrow another owned external tree')
    if selected.exists() and any(path.parent != selected for path in selected.rglob(_MARKER)):
        raise store.WorkerStateError('An explicit workspace contains another owned external tree')
    if not store._prove(selected).proven:
        raise store.WorkerStateError(
            f'Explicit browser workspace {requested} is not proven RAM. Its original contents were retained; '
            'import it with hermes secure-vault repair --browser-use-workspace before protected use.')
    try:
        store._identity(selected)
    except FileNotFoundError:
        store._identity(selected.parent)
    return {'external_tree':str(selected)}


class ExternalWorkerResource(store.WorkerResource):
    """The same WORKER codec and lifecycle, with an explicitly selected existing RAM tree."""
    def __init__(self, home, identity, custody, *, base=None):
        super().__init__(home, identity, custody, base=base)
        self.tree = Path(identity['external_tree'])

    def _marker(self):
        return {'version':1, 'home':str(self.home), 'resource':self.digest}

    def check_tree(self):
        if (self.tree.resolve() != self.tree or not store._prove(self.tree).proven
                or store._read(self.tree/_MARKER) != self._marker()):
            raise store.WorkerStateError('The explicit workspace lost its original home/path binding; all copies were retained')

    def _validate(self):
        super()._validate()
        document = store._read(self.path/store._BINDING)
        if document.get('external_tree') != {'path':str(self.tree), 'inode':list(store._identity(self.tree))}:
            raise store.WorkerStateError('The original explicit workspace inode was replaced; all copies were retained')
        self.check_tree()

    def bind(self, stage):
        super().bind(stage)
        document = store._read(stage/store._BINDING)
        document['external_tree'] = {'path':str(self.tree), 'inode':list(store._identity(self.tree))}
        store._write(stage/store._BINDING, document)

    def _claim_marker(self):
        try:
            descriptor = os.open(self.tree/_MARKER, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC, 0o600)
        except FileExistsError:
            self.check_tree()
            return
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(json.dumps(self._marker(), sort_keys=True).encode())
            handle.flush()
            os.fsync(handle.fileno())
        self.check_tree()

    def restore_initial(self, stage, operation):
        del stage  # The selected external path stays the actual workspace.
        if self.tree.resolve() != self.tree or not store._prove(self.tree).proven:
            raise store.WorkerStateError('The selected explicit workspace path changed before admission')
        if os.path.lexists(self.tree/_MARKER):
            self.check_tree()
        size = archive.archive_ciphertext_size(self.home, self.relative, digest=self.digest)
        if size is not None:
            expected = archive.replay_archive(self.home, self.relative, custody=operation, digest=self.digest)
            if os.path.lexists(self.tree) and any(self.tree.iterdir()):
                if archive.tree_digest(self.tree) != expected:
                    raise store.WorkerStateError('Explicit workspace differs from its verified archive; both copies retained for deliberate import')
            else:
                self._restore_ciphertext(expected, operation)
        try:
            store._identity(self.tree)
        except FileNotFoundError:
            operation.check()
            store._identity(self.tree.parent)
            self.tree.mkdir(mode=0o700)
        original = store._identity(self.tree)
        archive.tree_digest(self.tree)  # refuse unsupported links/special files without dropping them
        if store._identity(self.tree) != original:
            raise store.WorkerStateError('The selected explicit workspace inode changed before admission')
        self._claim_marker()

    def _restore_ciphertext(self, summary, operation):
        from hermes_security.protected_workspace import fresh_sibling_workspace, remove_stable_workspace

        store._require_restore_capacity(self.tree.parent, summary)
        original = store._identity(self.tree) if os.path.lexists(self.tree) else None
        staged = fresh_sibling_workspace(self.tree, 'restore', prove=store._prove)
        try:
            archive.replay_archive(self.home, self.relative, custody=operation, digest=self.digest, target=staged)
            operation.check()
            if original is None:
                if os.path.lexists(self.tree):
                    raise store.WorkerStateError('The selected explicit workspace appeared during restore; both copies retained')
            elif store._identity(self.tree) != original or any(self.tree.iterdir()):
                raise store.WorkerStateError('The selected explicit workspace changed during restore; both copies retained')
            os.replace(staged, self.tree)
        finally:
            if staged.exists():
                remove_stable_workspace(staged)



def existing_browser_use_resource(home, selected):
    """An explicitly chosen path may reuse its own profile's already owned Browser Use tree."""
    for root in (selected, *selected.parents):
        if not root.name.startswith(store.PREFIX):
            continue
        document = store._read(root/store._BINDING)
        identity = document.get('identity_doc')
        if document.get('home') != str(home) or not isinstance(identity, dict):
            raise store.WorkerStateError('Selected RAM path belongs to another profile; no payload read')
        if not str(identity.get('kind','')).startswith('browser-use-'):
            raise store.WorkerStateError('Selected RAM path belongs to a different protected producer; every copy retained')
        custody = archive.capture_custody(home)
        resource = store._construct(home, identity, custody, base=root.parent)
        resource._validate()
        if resource.path != root or document.get('identity') != resource.digest or not selected.is_relative_to(resource.tree):
            raise store.WorkerStateError('Selected Browser Use RAM tree has an invalid original binding')
        return resource
    return None
