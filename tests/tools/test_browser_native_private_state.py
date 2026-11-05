"""Native producer contracts with a declared fake vendor and unit RAM proof.

These tests exercise the real environment/capture/checkpoint adapters. The vendor byte producer
and backing-filesystem proof are explicit unit seams; actual PM Chromium runs in disposable VMs.
"""
from pathlib import Path
import os
import subprocess
import sys

import pytest

from hermes_security import vault

pytestmark = pytest.mark.platforms('linux', 'macos')


@pytest.fixture
def native_state(tmp_path, monkeypatch):
    from hermes_platform.memory_protection import RamProof
    from hermes_platform.memory_capacity import CapacityBudget
    from hermes_security import protected_workspace
    from tools import browser_tool as browser

    home, user, ram, disk_tmp = [tmp_path/name for name in ('owner', 'user', 'ram', 'disk-tmp')]
    for path in (home, user, ram, disk_tmp):
        path.mkdir(mode=0o700)
        path.chmod(0o700)
    monkeypatch.setenv('HOME', str(user))
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(vault, '_SCRYPT_N', 1024)
    vault.init_vault(home, 'synthetic native worker password')
    vault.unlock(home, 'synthetic native worker password')
    monkeypatch.setattr(protected_workspace, 'protected_workspace_base', lambda: ram)
    def proof(path):
        path = Path(path).resolve()
        proven = path == ram or path.is_relative_to(ram)
        return RamProof(proven, 'ramfs' if proven else 'ext4', 'declared unit RAM seam')
    monkeypatch.setattr('hermes_platform.memory_protection.ram_only_directory_proof', proof)
    monkeypatch.setattr('hermes_platform.memory_capacity.capacity_budget',
                        lambda *_a, **_k: CapacityBudget(8*1024**3, 'declared unit memory budget'))
    monkeypatch.setattr(browser, '_socket_safe_tmpdir', lambda: str(disk_tmp))
    monkeypatch.setattr(browser, '_build_browser_env', lambda: {'HOME':str(user), 'TMPDIR':str(disk_tmp), 'CI':'true'})
    monkeypatch.setattr('pm.env_for', lambda _name, base_env: base_env.copy())
    monkeypatch.setattr('hermes_cli.browser_runtime.chromium_executable', lambda: None)
    monkeypatch.setattr('tools.terminal_tool._get_env_config', lambda: {'env_type':'local'})
    yield home, user, ram
    from hermes_cli import browser_worker_store
    from tools import browser_native_runtime

    for key in list(browser_native_runtime._SESSIONS):
        if key[0] == home:
            lease = browser_native_runtime._SESSIONS.pop(key)
            browser_native_runtime._SOCKETS.pop(str(lease.socket), None)
    for key in list(browser_worker_store._POOL):
        if key[0] == home:
            browser_worker_store._POOL.pop(key)
    vault.clear_vault_cache()


def test_native_default_private_home_and_tmp_never_use_the_persistent_caller_home(native_state, monkeypatch):
    from tools import browser_tool_session as session

    home, user, ram = native_state
    payload = b'synthetic-private-native-auth-state'
    seen = []
    def vendor(_argv, *, env, **_kwargs):
        target = Path(env['HOME'])/'.agent-browser'/'sessions'/'synthetic.json'
        target.parent.mkdir(mode=0o700, parents=True)
        target.write_bytes(payload)
        seen.append(env.copy())
        return 0, b'{}', b''
    monkeypatch.setattr('hermes_platform.client_capture.run_client', vendor)
    socket = session._prepare_session_socket_dir('native-private-owner')
    env = session._agent_browser_command_env(socket)
    assert session._run_agent_browser_client(['synthetic-agent-browser'], env, None, 10) == (0, '{}', '')
    assert Path(seen[0]['HOME']).is_relative_to(ram)
    assert Path(seen[0]['TMPDIR']).is_relative_to(ram)
    assert Path(seen[0]['AGENT_BROWSER_SOCKET_DIR']).is_relative_to(ram)
    assert 'CI' not in seen[0]
    assert not any(payload in path.read_bytes() for root in (home, user) for path in root.rglob('*') if path.is_file())
    assert list((home/'browser-worker').glob('*.hbwa'))


def test_prepared_native_call_keeps_source_while_shared_config_can_serve_sibling(native_state, monkeypatch):
    from agent.session_context import scoped_current_session_id
    from hermes_security.session_authority import start_session_authority
    from hermes_security.errors import VaultLockedError
    from tools import browser_tool_session as session

    home, _user, _ram = native_state
    first = start_session_authority(home, 'native-source-A')
    sibling = start_session_authority(home, 'native-source-B')
    dispatched = []
    monkeypatch.setattr('hermes_platform.client_capture.run_client',
                        lambda *_a, **_k: dispatched.append(True) or (0, b'{}', b''))
    try:
        with scoped_current_session_id('native-source-A'):
            socket = session._prepare_session_socket_dir('native-shared-config')
            original = session._agent_browser_command_env(socket)
        first.close()
        with scoped_current_session_id('native-source-B'):
            with pytest.raises(VaultLockedError):
                session._run_agent_browser_client(['synthetic-agent-browser'], original, None, 10)
            assert dispatched == []
            own = session._agent_browser_command_env(socket)
            assert session._run_agent_browser_client(['synthetic-agent-browser'], own, None, 10)[0] == 0
        assert dispatched == [True] and vault.is_unlocked(home)
    finally:
        first.close()
        sibling.close()


def test_native_worker_config_cold_restore_preserves_bytes_and_owner_execute(native_state, monkeypatch):
    from hermes_cli import browser_worker_store as store
    from tools import browser_tool_session as session
    from tools import browser_native_runtime

    home, _user, _ram = native_state
    daemons = []
    def vendor(_argv, *, env, **_kwargs):
        root = Path(env['HOME'])/'.agent-browser'
        root.mkdir(mode=0o700, parents=True)
        (root/'auth.json').write_bytes(b'synthetic native auth state')
        helper = root/'helper.sh'
        helper.write_bytes(b'#!/bin/sh\nexit 0\n')
        helper.chmod(0o700)
        # A real owned synthetic daemon supplies kernel PID/start/executable/environment evidence.
        # It is not agent-browser and makes no browser qualification claim.
        child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read(1)'],
                                 env=dict(env), stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        daemons.append(child)
        (Path(env['AGENT_BROWSER_SOCKET_DIR'])/'native-cold-owner.pid').write_text(str(child.pid))
        return 0, b'{}', b''
    monkeypatch.setattr('hermes_platform.client_capture.run_client', vendor)
    socket = session._prepare_session_socket_dir('native-cold-owner')
    env = session._agent_browser_command_env(socket)
    try:
        session._run_agent_browser_client([sys.executable], env, None, 10)
    finally:
        for child in daemons:
            child.communicate(b'x', timeout=10)
    resource = env.lease.resource
    archive_before = (home/resource.relative).read_bytes()
    browser_native_runtime.stop_and_release('native-cold-owner', socket)
    report = store.retire_quiescent(home)
    assert report['archived'] == [resource.relative.as_posix()] and not report['retained']
    store._POOL.clear()
    restored = store.resource(home, kind='native-browser-config')
    with restored.operation():
        assert (restored.tree/'home'/'.agent-browser'/'auth.json').read_bytes() == b'synthetic native auth state'
        assert os.stat(restored.tree/'home'/'.agent-browser'/'helper.sh').st_mode & 0o7777 == 0o700
    assert (home/resource.relative).read_bytes() != archive_before  # fresh authenticated checkpoint, same private bytes


def test_inactive_callback_cannot_close_with_sibling_but_new_explicit_cleanup_can(native_state, monkeypatch):
    from agent.session_context import scoped_current_session_id
    from hermes_security.errors import VaultLockedError
    from hermes_security.session_authority import start_session_authority
    from tools import browser_tool as browser, browser_tool_lifecycle as lifecycle, browser_tool_session as session

    home, _user, _ram = native_state
    first = start_session_authority(home, 'native-cleanup-A')
    sibling = start_session_authority(home, 'native-cleanup-B')
    task = 'native-callback-original'
    seen = []
    try:
        with scoped_current_session_id('native-cleanup-A'):
            socket = session._prepare_session_socket_dir(task)
            session._agent_browser_command_env(socket)
        browser._active_sessions[task] = {'session_name':task, 'bb_session_id':None}
        browser._session_owner_homes[task] = str(home)
        monkeypatch.setattr(lifecycle._cdp, '_stop_cdp_supervisor', lambda *_a: seen.append('effect'))
        monkeypatch.setattr(lifecycle._recording, 'stop', lambda *_a: None)
        monkeypatch.setattr(lifecycle._session, '_run_browser_command', lambda *_a, **_k: seen.append('close'))
        first.close()
        with scoped_current_session_id('native-cleanup-B'):
            with pytest.raises(VaultLockedError):
                with lifecycle._session_owner_scope(task):
                    lifecycle._cleanup_single_browser_session(task)
            assert seen == [] and task in browser._active_sessions
            lifecycle._cleanup_single_browser_session(task)
        assert seen == ['effect', 'close'] and task not in browser._active_sessions
        assert vault.is_unlocked(home)
    finally:
        first.close()
        sibling.close()
        browser._active_sessions.pop(task, None)
        browser._session_owner_homes.pop(task, None)


@pytest.mark.parametrize('selection', ['HOME', 'XDG_CACHE_HOME', 'vendor-auth-root'])
def test_queued_native_directory_replacement_refuses_before_private_bytes_leave_ram(native_state, monkeypatch, selection):
    from tools import browser_tool_session as session

    home, user, ram = native_state
    calls = []
    def vendor(_argv, *, env, **_kwargs):
        calls.append(True)
        if len(calls) > 1:
            root = Path(env['HOME'])/'.agent-browser' if selection == 'vendor-auth-root' else Path(env[selection])
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            (root/'private-state').write_bytes(b'synthetic queued native private auth')
        return 0, b'{}', b''
    monkeypatch.setattr('hermes_platform.client_capture.run_client', vendor)
    socket = session._prepare_session_socket_dir('native-directory-admission')
    original = session._agent_browser_command_env(socket)
    selected = Path(original['HOME'])/'.agent-browser' if selection == 'vendor-auth-root' else Path(original[selection])
    selected.mkdir(mode=0o700, exist_ok=True)
    (selected/'original-state').write_bytes(b'original native data must remain available')
    session._run_agent_browser_client(['declared-unit-vendor'], original, None, 10)
    cipher = (home/original.lease.resource.relative).read_bytes()
    retained = ram/'retained-native-directory'
    selected.rename(retained)
    disk = user/'unrelated-persistent-directory'
    disk.mkdir(mode=0o700)
    selected.symlink_to(disk, target_is_directory=True)
    with pytest.raises(RuntimeError):
        session._run_agent_browser_client(['declared-unit-vendor'], original.copy(), None, 10)
    assert calls == [True] and not list(disk.iterdir())
    assert (retained/'original-state').read_bytes() == b'original native data must remain available'
    assert (home/original.lease.resource.relative).read_bytes() == cipher
