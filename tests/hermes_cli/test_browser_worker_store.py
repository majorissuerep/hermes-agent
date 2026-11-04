"""Owned WORKER resources preserve data across explicit legacy repair and refused operations.

RAM/memory facts use the labelled unit fixture; no native browser or real RAM claim is made here.
"""
from pathlib import Path
import os

import pytest

pytest_plugins = ['tests.tools.test_browser_native_private_state']
pytestmark = pytest.mark.platforms('linux', 'macos')


def test_explicit_native_import_verifies_before_retiring_source_and_keeps_encrypted_recovery(native_state):
    from hermes_cli.browser_worker_repair import import_native_home
    from hermes_security import browser_worker_archive as archive
    from tools import browser_tool_session as session

    home, user, _ram = native_state
    source = user/'.agent-browser'
    source.mkdir(mode=0o700)
    payload = b'synthetic old native browser credentials'
    (source/'auth.json').write_bytes(payload)
    helper = source/'helper.sh'
    helper.write_bytes(b'#!/bin/sh\nexit 0\n')
    helper.chmod(0o700)
    report = import_native_home(home, source)
    assert report['source_retired'] and not source.exists()
    assert payload not in (home/report['archive']).read_bytes()
    assert payload not in (home/report['recovery']).read_bytes()
    archive.replay_archive(home, report['recovery'], custody=archive.capture_custody(home))
    socket = session._prepare_session_socket_dir('after-explicit-import')
    env = session._agent_browser_command_env(socket)
    restored = Path(env['HOME'])/'.agent-browser'
    assert (restored/'auth.json').read_bytes() == payload
    assert (restored/'helper.sh').stat().st_mode & 0o7777 == 0o700
    before = (home/report['archive']).read_bytes()
    source.mkdir(mode=0o700)
    (source/'auth.json').write_bytes(b'later user data must not be ignored or overwritten')
    with pytest.raises(RuntimeError, match='already has an archive'):
        import_native_home(home, source)
    assert (home/report['archive']).read_bytes() == before
    assert (source/'auth.json').read_bytes() == b'later user data must not be ignored or overwritten'


def test_legacy_default_and_live_or_foreign_sources_refuse_without_mutating_user_data(native_state, tmp_path):
    from hermes_cli.browser_worker_repair import import_native_home
    from tools import browser_tool_session as session
    from hermes_security import vault

    home, user, _ram = native_state
    source = user/'.agent-browser'
    source.mkdir(mode=0o700)
    (source/'auth.json').write_bytes(b'synthetic original native auth')
    before = (source/'auth.json').read_bytes()
    socket = session._prepare_session_socket_dir('unimported-native')
    with pytest.raises(RuntimeError, match='native-browser-state'):
        session._agent_browser_command_env(socket)
    assert (source/'auth.json').read_bytes() == before
    assert not list(home.glob('browser-worker/*.hbwa'))
    from tools import browser_native_runtime
    with pytest.raises(RuntimeError, match='owner is live or unknown'):
        import_native_home(home, source)
    browser_native_runtime._SOCKETS[socket].resource.release()  # the fixture's Hermes owner closes first
    (source/'current.pid').write_text(str(os.getpid()))
    with pytest.raises(RuntimeError, match='Close the native browser'):
        import_native_home(home, source)
    assert (source/'auth.json').read_bytes() == before
    assert not list(home.glob('browser-worker/*.hbwa'))
    foreign = tmp_path/'foreign'
    foreign.mkdir(mode=0o700)
    vault.init_vault(foreign, 'synthetic foreign profile password')
    native = foreign/'.agent-browser'
    native.mkdir(mode=0o700)
    (native/'auth.json').write_bytes(b'foreign native credentials')
    with pytest.raises(RuntimeError, match='another encrypted profile'):
        import_native_home(home, native)
    assert (native/'auth.json').read_bytes() == b'foreign native credentials'


def test_refused_checkpoint_keeps_latest_ram_and_previous_archive_then_explicit_repair_can_recover(native_state):
    from agent.session_context import scoped_current_session_id
    from hermes_cli import browser_worker_store as store
    from hermes_security import browser_worker_archive as archive
    from hermes_security.session_authority import start_session_authority
    from hermes_security.errors import VaultLockedError

    home, _user, _ram = native_state
    source = start_session_authority(home, 'worker-original')
    selected = store.resource(home, kind='synthetic-worker-lifecycle')
    try:
        with scoped_current_session_id('worker-original'):
            with selected.operation():
                (selected.tree/'state').write_bytes(b'original persisted state')
        before = (home/selected.relative).read_bytes()
        with pytest.raises(store.WorkerStateError, match='RAM files were retained'):
            with scoped_current_session_id('worker-original'):
                with selected.operation():
                    (selected.tree/'state').write_bytes(b'latest RAM state must survive')
                    source.close()
        assert (home/selected.relative).read_bytes() == before
        assert (selected.tree/'state').read_bytes() == b'latest RAM state must survive'
        selected.release()
        with scoped_current_session_id(''):
            report = store.retire_quiescent(home)
        assert report['archived'] == [selected.relative.as_posix()] and report['retained'] == []
        assert not selected.path.exists()
        target = selected.base/'explicit-readback'
        target.mkdir(mode=0o700)
        archive.replay_archive(home, selected.relative, custody=archive.capture_custody(home), target=target)
        assert (target/'state').read_bytes() == b'latest RAM state must survive'
    finally:
        source.close()


def test_damaged_worker_archive_cannot_become_an_empty_config(native_state):
    from hermes_cli import browser_worker_store as store

    home, _user, _ram = native_state
    selected = store.resource(home, kind='damaged-config')
    with selected.operation():
        (selected.tree/'auth.json').write_bytes(b'private config to preserve')
    selected.release()
    assert store.retire_quiescent(home)['archived'] == [selected.relative.as_posix()]
    path = home/selected.relative
    path.write_bytes(path.read_bytes()[:-1])
    damaged = path.read_bytes()
    store._POOL.clear()
    later = store.resource(home, kind='damaged-config')
    with pytest.raises(RuntimeError):
        with later.operation():
            pytest.fail('A damaged archive became an empty replacement')
    assert path.read_bytes() == damaged and not later.path.exists()


def test_missing_ram_requires_explicit_checkpoint_recovery_and_keeps_passive_history(native_state):
    from hermes_cli import browser_worker_store as store, backup_browser_profiles as backup
    from hermes_security import browser_worker_pending as pending
    from hermes_security.protected_workspace import remove_stable_workspace
    from hermes_state.runtime_ownership import OwnershipConflict

    home, _user, _ram = native_state
    selected = store.resource(home, kind='missing-volatile-state')
    with selected.operation():
        (selected.tree/'state').write_bytes(b'last verified checkpoint')
    selected.release()
    from hermes_security import migrate
    assert migrate.scan_for_plaintext(home) == []
    original_archive = (home/selected.relative).read_bytes()
    original_locator = (home/pending.relative(selected.digest)).read_bytes()
    remove_stable_workspace(selected.path)  # actual loss, not a successful ordinary retirement
    with pytest.raises(RuntimeError, match='repair'):
        with selected.operation():
            pytest.fail('Missing volatile state was silently replaced')
    # An older backup with no worker entries also must not bypass local recovery debt.
    with pytest.raises(OwnershipConflict):
        with backup.restore_guard(home, [Path('config.yaml')]):
            pytest.fail('Restore bypassed active missing-RAM reference')
    assert (home/pending.relative(selected.digest)).read_bytes() == original_locator
    report = store.retire_quiescent(home)
    assert report['missing'] and not report['archived'] and not report['retained']
    assert (home/selected.relative).read_bytes() == original_archive
    assert not pending.paths(home) and len(pending.paths(home, kind='recovery')) == 1
    note = pending.paths(home, kind='recovery')[0]
    document = pending.read(home, note.relative_to(home), custody=selected.custody)
    assert document['path'] == str(selected.path) and 'unverifiable' in document['outcome']
    assert migrate.scan_for_plaintext(home) == []
    entries = list(backup.current_entries(home, Path('.')))
    assert (note, note.relative_to(home)) in entries
    with note.open('rb') as handle:
        from hermes_security import browser_worker_archive
        browser_worker_archive.authenticate_ciphertext(handle, note.relative_to(home), custody=selected.custody)
    with selected.operation():
        assert (selected.tree/'state').read_bytes() == b'last verified checkpoint'


def test_pending_reference_preserves_original_ram_base_and_unknown_launch_nonmutation(native_state, monkeypatch):
    from hermes_cli import browser_worker_store as store
    from hermes_security import browser_worker_pending as pending
    from hermes_security.protected_workspace import remove_stable_workspace

    home, _user, ram = native_state
    selected = store.resource(home, kind='original-ram-reference')
    with selected.operation() as call:
        (selected.tree/'state').write_bytes(b'latest recoverable RAM')
        store._write(selected.path/'processes'/'unknown-fixture',
                     {'unknown':True, 'session':'fixture', 'executable':'/synthetic/vendor',
                      'socket':str(selected.path/'r'), 'socket_inode':list(store._identity(selected.path/'r'))})
        pending.sync(selected, call)
    selected.release()
    alternate = ram/'new-base'
    alternate.mkdir(mode=0o700)
    monkeypatch.setattr('hermes_security.protected_workspace.protected_workspace_base', lambda: alternate)
    assert [item.path for item in store.resources_in_ram(home)] == [selected.path]
    report = store.retire_quiescent(home)
    assert report['retained'] and selected.path.exists()
    before = (home/pending.relative(selected.digest)).read_bytes()
    archived = (home/selected.relative).read_bytes()
    remove_stable_workspace(selected.path)
    report = store.retire_quiescent(home)
    assert report['retained'] and not report['missing']
    assert (home/pending.relative(selected.digest)).read_bytes() == before
    assert (home/selected.relative).read_bytes() == archived
