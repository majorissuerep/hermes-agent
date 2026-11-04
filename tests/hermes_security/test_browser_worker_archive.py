"""Worker trees preserve arbitrary files in a distinct authenticated owner domain."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil

import pytest

from hermes_security import browser_profile_archive as profiles
from hermes_security import browser_worker_archive as workers
from hermes_security import vault
from hermes_security.errors import VaultError
from hermes_security.tree_archive import ArchiveError

PASSWORD = 'synthetic browser worker archive password'


def _owner(path):
    path.mkdir(mode=0o700, parents=True)
    path.chmod(0o700)
    vault.init_vault(path, PASSWORD)
    vault.unlock(path, PASSWORD)
    return path


def _files(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, '_SCRYPT_N', 1024)
    home = _owner(tmp_path/'owner')
    monkeypatch.setenv('HERMES_HOME', str(home))
    try:
        yield home
    finally:
        vault.clear_vault_cache()


@pytest.mark.platforms('posix')
def test_worker_retains_all_regular_files_and_refuses_other_archive_domains(owner, tmp_path, monkeypatch):
    source, restored = tmp_path/'source', tmp_path/'restored'
    for directory in (source, restored):
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)
    payloads = {'Default/Cache/sole-copy.tmp': b'worker transport-shaped application data',
                'Default/Code Cache/opaque': b'worker code cache named app data',
                'Default/GPUCache/opaque': b'worker GPU cache named app data',
                'Default/Service Worker/CacheStorage/offline': b'offline application state',
                'custom/Cache/unknown.tmp': bytes(range(256))*5000,
                'SingletonLock': b'an ordinary worker file, not a live Chromium marker',
                'workspace/agent_helpers.py': b'def retained_helper(): return 19\n',
                'empty-file': b''}
    for name, data in payloads.items():
        path = source/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (source/'empty-directory').mkdir()
    custody = workers.capture_custody(owner)
    alias = tmp_path/'owner-alias'
    alias.symlink_to(owner, target_is_directory=True)
    assert workers.capture_custody(alias).vault.home == owner
    identity = {'workspace': 'synthetic owned workspace'}
    digest = workers.identity_digest(custody, identity)
    relative = workers.archive_relative(digest)
    summary = workers.write_archive(alias, relative, source, custody=custody, identity=identity, digest=digest)
    assert workers.replay_archive(alias, relative, custody=custody, digest=digest, target=restored) == summary
    assert _files(restored) == _files(source)
    assert (restored/'empty-directory').is_dir()
    assert summary == workers.tree_digest(source) == workers.tree_digest(restored)
    expected = _files(owner)
    with monkeypatch.context() as bounds:
        bounds.setattr(workers, 'MAX_STATE_BYTES', 8)
        with pytest.raises(ArchiveError, match='persistence bound'):
            workers.write_archive(owner, relative, source, custody=custody, identity=identity, digest=digest)
    assert _files(owner) == expected
    assert _files(source) == _files(restored)
    other_digest = workers.identity_digest(custody, {'workspace': 'a different owned workspace'})
    other = workers.archive_relative(other_digest)
    shutil.copyfile(owner/relative, owner/other)
    (owner/other).chmod(0o600)
    with pytest.raises(VaultError):
        workers.replay_archive(owner, other, custody=custody, digest=other_digest)
    expected = _files(owner)
    # Wrong domain paths must fail before deriving a decryption key.
    def no_key(_purpose):
        pytest.fail('A foreign archive path reached key derivation')
    monkeypatch.setattr(custody, 'derive_key', no_key)
    with pytest.raises(PermissionError):
        profiles.replay_archive(owner, relative, custody=custody)
    with pytest.raises(PermissionError):
        workers.replay_archive(owner, Path('browser-profile/chrome-debug.hbpa'), custody=custody, digest=digest)
    assert _files(owner) == expected


def test_worker_old_custody_cannot_revive_after_human_unlock_and_sibling_survives(owner, tmp_path):
    sibling = _owner(tmp_path/'sibling')
    custody = workers.capture_custody(owner)
    sibling_custody = workers.capture_custody(sibling)
    source = tmp_path/'source'
    source.mkdir(mode=0o700)
    (source/'state.tmp').write_bytes(b'original worker state')
    identity = {'workspace': 'original worker'}
    digest = workers.identity_digest(custody, identity)
    relative = workers.archive_relative(digest)
    workers.write_archive(owner, relative, source, custody=custody, identity=identity, digest=digest)
    vault.lock_now(owner)
    vault.unlock(owner, PASSWORD)
    before = _files(owner)
    (source/'state.tmp').write_bytes(b'new bytes must not be sealed by old custody')
    with pytest.raises(VaultError):
        workers.write_archive(owner, relative, source, custody=custody, identity=identity, digest=digest)
    assert _files(owner) == before
    assert (source/'state.tmp').read_bytes() == b'new bytes must not be sealed by old custody'
    fresh = workers.capture_custody(owner)
    fresh_digest = workers.identity_digest(fresh, {'workspace': 'fresh human owner'})
    workers.write_archive(owner, workers.archive_relative(fresh_digest), source, custody=fresh,
                          identity={'workspace': 'fresh human owner'}, digest=fresh_digest)
    sibling_digest = workers.identity_digest(sibling_custody, {'workspace': 'independent sibling'})
    sibling_path = workers.archive_relative(sibling_digest)
    workers.write_archive(sibling, sibling_path, source, custody=sibling_custody,
                          identity={'workspace': 'independent sibling'}, digest=sibling_digest)
    assert workers.replay_archive(sibling, sibling_path, custody=sibling_custody, digest=sibling_digest).bytes > 0
    nested = owner/'profiles'/'missing-own-vault'
    nested.mkdir(parents=True)
    before = _files(owner)
    with pytest.raises(VaultError):
        workers.capture_custody(nested)
    assert _files(owner) == before
    assert workers.replay_archive(sibling, sibling_path, custody=sibling_custody, digest=sibling_digest).bytes > 0
