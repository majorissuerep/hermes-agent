"""Explicit RAM workspace path/data contracts; backing proof is a declared unit seam."""
from pathlib import Path

import pytest

pytest_plugins = ['tests.tools.test_browser_native_private_state']
pytestmark = pytest.mark.platforms('linux', 'macos')


def test_exact_external_workspace_preserves_all_files_execute_and_cold_path(native_state):
    from hermes_cli import browser_worker_store as store

    home, _user, ram = native_state
    requested = ram/'author-chosen-workspace'
    requested.mkdir(mode=0o700)
    payload = b'synthetic complete author workspace state'
    (requested/'CacheStorage.tmp').write_bytes(payload)
    helper = requested/'agent_helpers.py'
    helper.write_bytes(b'def answer(): return 37\n')
    helper.chmod(0o700)
    selected = store.resource(home, kind='browser-use-external-workspace', task='task-A', external_tree=requested)
    with selected.operation():
        assert selected.tree == requested
        assert (selected.tree/'CacheStorage.tmp').read_bytes() == payload
    assert payload not in (home/selected.relative).read_bytes()
    selected.release()
    assert store.retire_quiescent(home)['archived'] == [selected.relative.as_posix()]
    assert not requested.exists()
    store._POOL.clear()
    fresh = store.resource(home, kind='browser-use-external-workspace', task='task-A', external_tree=requested)
    with fresh.operation():
        assert fresh.tree == requested
        assert (requested/'CacheStorage.tmp').read_bytes() == payload
        assert (requested/'agent_helpers.py').stat().st_mode & 0o7777 == 0o700


def test_external_workspace_refuses_ssd_foreign_profile_and_retarget_without_mutation(native_state, tmp_path):
    from hermes_cli import browser_worker_store as store
    from hermes_security import vault

    home, user, ram = native_state
    disk = user/'workspace'
    disk.mkdir(mode=0o700)
    (disk/'only-copy.tmp').write_bytes(b'synthetic original SSD workspace')
    with pytest.raises(RuntimeError, match='not proven RAM'):
        store.resource(home, kind='browser-use-external-workspace', external_tree=disk)
    assert (disk/'only-copy.tmp').read_bytes() == b'synthetic original SSD workspace'
    requested = ram/'explicit-workspace'
    requested.mkdir(mode=0o700)
    selected = store.resource(home, kind='browser-use-external-workspace', external_tree=requested)
    with selected.operation():
        (selected.tree/'state').write_bytes(b'original profile workspace')
    old = (home/selected.relative).read_bytes()
    foreign = tmp_path/'foreign-profile'
    foreign.mkdir(mode=0o700)
    vault.init_vault(foreign, 'synthetic foreign workspace password')
    vault.unlock(foreign, 'synthetic foreign workspace password')
    other = store.resource(foreign, kind='browser-use-external-workspace', external_tree=requested)
    with pytest.raises(RuntimeError, match='original home/path binding'):
        with other.operation():
            pytest.fail('Another profile borrowed an already-owned workspace')
    moved = ram/'retained-original'
    requested.rename(moved)
    requested.mkdir(mode=0o700)
    with pytest.raises(RuntimeError, match='replaced'):
        with selected.operation():
            pytest.fail('A replacement directory renewed the original operation')
    assert (moved/'state').read_bytes() == b'original profile workspace'
    assert (home/selected.relative).read_bytes() == old


def test_interrupted_explicit_workspace_retirement_remains_repairable_from_verified_cipher(native_state, monkeypatch):
    from hermes_cli import browser_worker_store as store
    from hermes_cli import browser_worker_retirement as retirement

    home, _user, ram = native_state
    requested = ram/'interrupted-explicit-workspace'
    requested.mkdir(mode=0o700)
    selected = store.resource(home, kind='browser-use-external-workspace', external_tree=requested)
    with selected.operation():
        (requested/'sole-copy').write_bytes(b'preserved through interrupted cleanup')
    selected.release()
    remove = retirement._remove_target
    def interrupted(target, custody):
        if Path(target['path']) == requested:
            (requested/'.hermes-browser-worker-owner.json').unlink()
            raise OSError('declared interrupted RAM cleanup')
        return remove(target, custody)
    monkeypatch.setattr(retirement, '_remove_target', interrupted)
    first = store.retire_quiescent(home)
    assert first['retained'] and not first['archived']
    archive_before = (home/selected.relative).read_bytes()
    assert (requested/'sole-copy').read_bytes() == b'preserved through interrupted cleanup'
    monkeypatch.setattr(retirement, '_remove_target', remove)
    second = store.retire_quiescent(home)
    assert second['archived'] == [selected.relative.as_posix()] and not second['retained']
    assert not selected.path.exists() and not requested.exists()
    assert (home/selected.relative).read_bytes() == archive_before


@pytest.mark.parametrize('replacement', ['directory', 'ciphertext'])
def test_pending_retirement_never_deletes_a_replacement_or_unauthenticated_tree(native_state, monkeypatch, replacement):
    from hermes_cli import browser_worker_retirement as retirement, browser_worker_store as store
    from hermes_security import browser_worker_pending as pending

    home, _user, ram = native_state
    requested = ram/'retain-if-replaced'
    requested.mkdir(mode=0o700)
    selected = store.resource(home, kind='browser-use-external-workspace', external_tree=requested)
    with selected.operation():
        (requested/'sole-copy').write_bytes(b'do not remove unverifiable state')
    selected.release()
    original = retirement._remove_target
    def interrupted(*_args):
        raise OSError('declared cleanup interruption')
    monkeypatch.setattr(retirement, '_remove_target', interrupted)
    assert store.retire_quiescent(home)['retained']
    monkeypatch.setattr(retirement, '_remove_target', original)
    cipher = home/selected.relative
    retained = requested
    if replacement == 'directory':
        retained = ram/'original-retained-tree'
        requested.rename(retained)
        requested.mkdir(mode=0o700)
        (requested/'replacement').write_bytes(b'new unrelated directory must survive')
    else:
        data = bytearray(cipher.read_bytes())
        data[-1] ^= 1
        cipher.write_bytes(data)
    before = cipher.read_bytes()
    refused = store.retire_quiescent(home)
    assert refused['retained'] and not refused['archived'] and pending.paths(home)
    assert cipher.read_bytes() == before and (retained/'sole-copy').read_bytes() == b'do not remove unverifiable state'
    if replacement == 'directory':
        assert (requested/'replacement').read_bytes() == b'new unrelated directory must survive'


def test_foreign_external_marker_refuses_before_payload_read(native_state, tmp_path, monkeypatch):
    from hermes_cli import browser_worker_store as store
    from hermes_security import vault, browser_worker_archive as archive

    home, _user, ram = native_state
    requested = ram/'foreign-payload-must-not-be-read'; requested.mkdir(mode=0o700)
    original = store.resource(home, kind='browser-use-explicit-workspace', external_tree=requested)
    with original.operation():
        (requested/'only-copy').write_bytes(b'original profile private contents')
    cipher = (home/original.relative).read_bytes()
    other = tmp_path/'another-owned-profile'; other.mkdir(mode=0o700)
    vault.init_vault(other, 'synthetic foreign-home refusal fixture')
    vault.unlock(other, 'synthetic foreign-home refusal fixture')
    selected = store.resource(other, kind='browser-use-explicit-workspace', external_tree=requested)
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Foreign profile reached the private payload reader')
    monkeypatch.setattr(archive, 'tree_digest', forbidden)
    with pytest.raises(store.WorkerStateError, match='original home/path binding'):
        with selected.operation():
            pytest.fail('Foreign profile acquired a shared path from its ambient key')
    assert (requested/'only-copy').read_bytes() == b'original profile private contents'
    assert (home/original.relative).read_bytes() == cipher and vault.is_unlocked(home)
