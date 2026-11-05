"""Cleanup removes only survivors proved by the full checkpoint and original controls."""
from pathlib import Path
import os

import pytest

pytest_plugins = ['tests.tools.test_browser_native_private_state']
pytestmark = pytest.mark.platforms('linux', 'macos')


def _interrupted(home, monkeypatch):
    from hermes_cli import browser_worker_retirement as retirement, browser_worker_store as store

    selected = store.resource(home, kind='retirement-survivor-contract')
    with selected.operation():
        (selected.tree/'progress').write_bytes(b'original verified progress')
    selected.release()
    remove = retirement._remove_target

    def interrupted(*_args):
        raise OSError('declared interruption before cleanup')

    monkeypatch.setattr(retirement, '_remove_target', interrupted)
    assert store.retire_quiescent(home)['retained']
    monkeypatch.setattr(retirement, '_remove_target', remove)
    return selected


def _inventory(root):
    return {path.relative_to(root).as_posix():path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


@pytest.mark.parametrize('change', ['new-file', 'changed-file', 'new-claim', 'changed-control', 'owner-exec'])
def test_changed_survivor_refuses_before_any_cleanup(native_state, monkeypatch, change):
    from hermes_cli import browser_worker_store as store
    from hermes_platform.processes import get_process_start_time

    home, _user, _ram = native_state
    selected = _interrupted(home, monkeypatch)
    cipher = (home/selected.relative).read_bytes()
    changes = {
        'new-file': lambda: (selected.tree/'new-sole-copy').write_bytes(b'private progress after interruption'),
        'changed-file': lambda: (selected.tree/'progress').write_bytes(b'changed private progress after interruption'),
        'owner-exec': lambda: (selected.tree/'progress').chmod(0o700),
        'changed-control': lambda: (selected.path/'.worker-binding.json').write_bytes(b'changed control state'),
        'new-claim': lambda: store._write(selected.path/'claims'/str(os.getpid()),
                                         {'pid':os.getpid(), 'start_time':get_process_start_time(os.getpid())}),
    }
    changes[change]()
    before = _inventory(selected.path)
    refused = store.retire_quiescent(home)
    assert refused['retained'] and not refused['archived']
    assert _inventory(selected.path) == before
    assert (home/selected.relative).read_bytes() == cipher


@pytest.mark.parametrize('failure', ['capacity', 'archive-parent', 'target-parent', 'post-hash-write'])
def test_proof_prerequisite_or_path_race_preserves_every_remaining_copy(native_state, monkeypatch, failure):
    from hermes_cli import browser_worker_retirement as retirement, browser_worker_store as store
    from hermes_cli import browser_worker_retirement_proof as proof

    home, _user, ram = native_state
    selected = _interrupted(home, monkeypatch)
    cipher = (home/selected.relative).read_bytes()
    source = selected.path
    if failure == 'capacity':
        def unavailable(*_args):
            raise store.WorkerStateError('Declared unavailable proof capacity')
        monkeypatch.setattr(proof, 'capacity', unavailable)
    elif failure == 'post-hash-write':
        original = proof.file_hash
        def changed_after_hash(parent, name, check):
            result = original(parent, name, check)
            if name == 'progress' and os.fstat(parent).st_ino == selected.tree.stat().st_ino:
                (selected.tree/'progress').write_bytes(b'new sole copy after hash')
            return result
        monkeypatch.setattr(proof, 'file_hash', changed_after_hash)
    else:
        remove = retirement._remove_target
        def changed_parent(target, guard):
            parent = (home/selected.relative).parent if failure == 'archive-parent' else ram
            replacement = parent.with_name(parent.name+'-original-retained')
            parent.rename(replacement)
            parent.mkdir(mode=0o700)
            if failure == 'archive-parent':
                # Same ciphertext bytes under a replacement parent must not revive the held FD.
                for path in replacement.iterdir():
                    if path.is_file():
                        copied = parent/path.name
                        copied.write_bytes(path.read_bytes())
                        copied.chmod(0o600)
            return remove(target, guard)
        monkeypatch.setattr(retirement, '_remove_target', changed_parent)
    refused = store.retire_quiescent(home)
    assert refused['retained'] and not refused['archived']
    if failure == 'target-parent':
        source = ram.with_name(ram.name+'-original-retained')/selected.path.name
    assert (source/'tree'/'progress').is_file()
    expected = b'new sole copy after hash' if failure == 'post-hash-write' else b'original verified progress'
    assert (source/'tree'/'progress').read_bytes() == expected
    assert (home/selected.relative).read_bytes() == cipher


def test_proofless_development_retirement_is_passive_history_not_cleanup_authority(native_state, monkeypatch):
    from hermes_security import browser_worker_pending as pending
    from hermes_security import browser_worker_archive as archive
    from hermes_security.errors import VaultIntegrityError
    from hermes_cli import browser_worker_store as store

    home, _user, _ram = native_state
    selected = _interrupted(home, monkeypatch)
    custody = archive.capture_custody(home)
    document = pending.read_optional(home, selected.digest, custody=custody)
    old_intent = dict(document['retirement'])
    old_intent.pop('control')
    old_intent['targets'] = [{key:value for key, value in target.items() if key != 'parent_inode'}
                             for target in old_intent['targets']]
    old = {**document, 'version':2, 'retirement':old_intent}
    relative = Path(archive.ARCHIVE_ROOT, '.recovered-'+selected.digest+'-'+'1'*16+'.json')
    pending.write(home, relative, {**old, 'outcome':'Unreleased proofless development history'}, custody=custody)
    with (home/relative).open('rb') as handle:
        assert archive.authenticate_ciphertext(handle, relative, custody=custody)['version'] == 2
    original = _inventory(selected.path)
    cipher = (home/selected.relative).read_bytes()
    pending.write(home, pending.relative(selected.digest), old, custody=custody)
    with pytest.raises(VaultIntegrityError, match='lacks survivor proof'):
        store.retire_quiescent(home)
    assert _inventory(selected.path) == original
    assert (home/selected.relative).read_bytes() == cipher


@pytest.mark.parametrize('normalized', ['mtime', 'group-read'])
def test_payload_proof_uses_only_published_codec_attributes(native_state, monkeypatch, normalized):
    from hermes_cli import browser_worker_store as store

    home, _user, _ram = native_state
    selected = _interrupted(home, monkeypatch)
    original = (home/selected.relative).read_bytes()
    path = selected.tree/'progress'
    if normalized == 'mtime':
        os.utime(path, (42, 42))
    else:
        path.chmod(0o640)
    result = store.retire_quiescent(home)
    assert result['archived'] == [selected.relative.as_posix()] and not result['retained']
    assert not selected.path.exists()
    assert (home/selected.relative).read_bytes() == original


@pytest.mark.parametrize('absence', ['target', 'parent', 'replaced-parent', 'denied-parent'])
def test_only_proven_absence_releases_missing_copy_debt(native_state, monkeypatch, absence):
    from hermes_cli import browser_worker_store as store
    from hermes_cli import browser_worker_retirement_proof as proof
    from hermes_security import browser_worker_pending as pending

    home, _user, ram = native_state
    selected = _interrupted(home, monkeypatch)
    before = (home/selected.relative).read_bytes()
    import shutil
    shutil.rmtree(selected.path)
    if absence == 'parent':
        shutil.rmtree(ram)
    elif absence == 'replaced-parent':
        ram.rename(ram.with_name(ram.name+'-old'))
        ram.mkdir(mode=0o700)
    elif absence == 'denied-parent':
        native_stat = Path.stat
        def denied(path, *args, **kwargs):
            if path == ram:
                raise PermissionError('Declared inaccessible original RAM parent')
            return native_stat(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'stat', denied)
    def no_staging(*_args):
        raise AssertionError('Missing-copy recovery must not require a new plaintext copy')
    monkeypatch.setattr(proof, 'capacity', no_staging)
    result = store.retire_quiescent(home)
    assert (home/selected.relative).read_bytes() == before
    if absence in {'replaced-parent', 'denied-parent'}:
        assert result['retained'] and not result['archived'] and pending.paths(home)
    else:
        assert not result['retained'] and len(result['archived']) == 1 and not pending.paths(home)
        assert 'uncheckpointed changes unverifiable' in result['archived'][0]
        history = pending.read(home, pending.paths(home, kind='recovery')[0].relative_to(home), custody=selected.custody)
        assert 'RAM targets absent' in history['outcome'] and 'retirement completed' not in history['outcome']
