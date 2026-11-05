"""Lightpanda private-state contracts with a fake vendor and labelled unit RAM proof.

No Lightpanda executable or native application runs here. Native qualification is separate.
"""
from pathlib import Path
import os

import pytest

pytest_plugins = ['tests.tools.test_browser_native_private_state']
pytestmark = pytest.mark.platforms('linux', 'macos')


class FakeVendor:
    def __init__(self, argv, *, env, stderr, **kwargs):
        self.pid, self.argv, self.env = 456789, list(argv), dict(env)
        self.stopped = False
        stderr.write(b'synthetic-lightpanda-private-cache-canary\n')
        stderr.flush()

    def poll(self):
        return 0 if self.stopped else None

    def terminate(self):
        self.stopped = True

    kill = terminate

    def wait(self, timeout=None):
        self.stopped = True
        return 0

    def cancel(self, timeout=None):
        self.stopped = True
        return 130


@pytest.fixture
def fake_lightpanda(native_state, monkeypatch):
    from tools import browser_lightpanda as lp
    from hermes_platform import managed_supervision

    launched = []
    def vendor(*args, **kwargs):
        child = FakeVendor(*args, **kwargs)
        child.pid += len(launched)
        launched.append(child)
        return child
    monkeypatch.setattr(lp.subprocess, 'Popen', vendor)
    monkeypatch.setattr(managed_supervision, 'SupervisedChild', vendor)
    monkeypatch.setattr(lp, 'find_lightpanda_binary', lambda: '/fixture/lightpanda')
    monkeypatch.setattr(lp, '_binary_supports_http_cache', lambda *_a, **_k: True)
    monkeypatch.setattr(lp, '_pick_free_loopback_port', lambda: 43117+len(launched))
    monkeypatch.setattr(lp, '_cdp_ready', lambda *_a, **_k: True)
    monkeypatch.setattr(lp, '_safe_start_time', lambda _pid: 123, raising=False)
    monkeypatch.setattr(lp, '_tree_kill', lambda *_a, **_k: None, raising=False)
    from hermes_platform import processes
    original_dead = processes.process_record_is_dead
    def dead(record):
        for child in launched:
            if record.get('pid') == child.pid:
                return child.stopped
        return original_dead(record)
    monkeypatch.setattr(processes, 'process_record_is_dead', dead)
    def identity(child, binary, argv, env):
        return {'pid':child.pid, 'start_time':123, 'executable':binary,
                'argv':list(argv), 'port':int(argv[argv.index('--port')+1])}
    monkeypatch.setattr(lp, '_process_record', identity, raising=False)
    monkeypatch.setattr('hermes_platform.processes.get_process_start_time',
                        lambda _pid: 123)
    monkeypatch.setattr(lp, '_record_matches', lambda _record: True, raising=False)
    yield lp, launched
    lp._servers.clear()


def test_vendor_cache_home_tmp_and_stderr_stay_in_private_ram(fake_lightpanda, native_state):
    lp, launched = fake_lightpanda
    home, user, ram = native_state
    first, error = lp.launch_lightpanda('first')
    assert error is None and first is not None
    second, error = lp.launch_lightpanda('second')
    assert error is None and second is not None
    caches = [Path(p.argv[p.argv.index('--http-cache-dir')+1]) for p in launched]
    assert caches[0] == caches[1], 'HTTP cache remains shared across named sessions'
    for child in launched:
        assert all(Path(child.env[key]).is_relative_to(ram) for key in ('HOME','TMPDIR','TMP','TEMP'))
    assert caches[0].is_relative_to(ram)
    assert Path(first.log_path).is_relative_to(ram) and Path(second.log_path).is_relative_to(ram)
    assert not any(b'synthetic-lightpanda-private-cache-canary' in p.read_bytes()
                   for root in (home,user) for p in root.rglob('*') if p.is_file())


def test_failed_scoped_environment_never_falls_back_to_ambient_authority(native_state, monkeypatch):
    from tools import browser_lightpanda as lp, browser_tool

    def refused():
        raise RuntimeError('synthetic selected-profile environment unavailable')
    monkeypatch.setattr(browser_tool, '_build_browser_env', refused)
    monkeypatch.setenv('__HERMES_INSTALL_OWNER_FD', '97')
    with pytest.raises(RuntimeError, match='selected-profile environment unavailable'):
        lp._browser_env()


def test_source_A_callback_is_revoked_but_fresh_B_can_keep_the_shared_server(fake_lightpanda, native_state):
    from agent.session_context import scoped_current_session_id
    from hermes_security.session_authority import start_session_authority
    from hermes_security.errors import VaultLockedError

    lp, launched = fake_lightpanda
    home, _user, _ram = native_state
    a = start_session_authority(home, 'lp-A')
    b = start_session_authority(home, 'lp-B')
    try:
        with scoped_current_session_id('lp-A'):
            server, error = lp.launch_lightpanda('shared')
        assert error is None
        info = {'session_name':'shared', 'features':{'lightpanda':True}, 'lightpanda_epoch':server.lease.token}
        with scoped_current_session_id('lp-B'):
            assert lp.get_server('shared') is server and server.is_alive()
        a.close()
        with pytest.raises(VaultLockedError):
            with lp.cleanup_scope(info, home=home, original=True):
                lp.stop_lightpanda('shared')
        assert not launched[0].stopped
        with scoped_current_session_id('lp-B'):
            assert lp.get_server('shared') is server and server.is_alive()
            lp.stop_lightpanda('shared', expected=server.lease.token)
        assert launched[0].stopped
    finally:
        a.close(); b.close()


def test_last_quiescent_close_checkpoints_and_cold_restore_keeps_cache_and_diagnostics(fake_lightpanda, native_state):
    from hermes_cli import browser_worker_store as store
    from hermes_security import browser_worker_archive as archive

    lp, launched = fake_lightpanda
    home, _user, _ram = native_state
    server, error = lp.launch_lightpanda('first')
    assert error is None
    payload = b'private LP cached user content'
    (server.lease.cache/'content').write_bytes(payload)
    previous = (home/server.lease.resource.relative).read_bytes()
    lp.stop_lightpanda('first', expected=server.lease.token)
    saved = (home/server.lease.resource.relative).read_bytes()
    assert saved != previous and payload not in saved
    assert store.retire_quiescent(home)['retained'] == []
    assert not server.lease.resource.path.exists()
    later, error = lp.launch_lightpanda('cold')
    assert error is None
    assert (later.lease.cache/'content').read_bytes() == payload
    assert list(later.lease.resource.tree.rglob('*.log'))
    archive.replay_archive(home, later.lease.resource.relative, custody=archive.capture_custody(home))
    lp.stop_lightpanda('cold', expected=later.lease.token)
    assert all(child.stopped for child in launched)


def test_failed_checkpoint_retains_tracking_ram_ref_and_exact_prior_cipher(fake_lightpanda, native_state, monkeypatch):
    from hermes_security import browser_worker_archive as archive
    from hermes_security import browser_worker_pending as pending

    lp, launched = fake_lightpanda
    home, _user, _ram = native_state
    server, error = lp.launch_lightpanda('unsaved')
    assert error is None
    path = home/server.lease.resource.relative
    previous = path.read_bytes()
    latest = b'latest private cache work'
    (server.lease.cache/'new').write_bytes(latest)
    def refused(*args, **kwargs):
        raise OSError('synthetic archive publication refusal')
    with monkeypatch.context() as patch:
        patch.setattr(archive, 'write_archive', refused)
        with pytest.raises(OSError, match='publication refusal'):
            lp.stop_lightpanda('unsaved', expected=server.lease.token)
    assert lp.get_server('unsaved') is server and launched[0].stopped
    assert path.read_bytes() == previous and (server.lease.cache/'new').read_bytes() == latest
    assert pending.paths(home)
    lp.stop_lightpanda('unsaved', expected=server.lease.token)
    assert lp.get_server('unsaved') is None
    assert path.read_bytes() != previous


def test_lock_reunlock_never_revives_old_server_or_reuses_its_live_ram(fake_lightpanda, native_state):
    from hermes_security import vault
    from hermes_security.errors import VaultError

    lp, launched = fake_lightpanda
    home, _user, _ram = native_state
    server, error = lp.launch_lightpanda('original')
    assert error is None
    before = (home/server.lease.resource.relative).read_bytes()
    vault.lock_now(home)
    vault.unlock(home, 'synthetic native worker password')
    with pytest.raises(VaultError):
        server.is_alive()
    replacement, error = lp.launch_lightpanda('new')
    assert replacement is None and 'earlier Lightpanda unlock' in error
    assert len(launched) == 1 and not launched[0].stopped
    assert (home/server.lease.resource.relative).read_bytes() == before


@pytest.mark.parametrize('name', ['../escape','foreign/session','.',r'foreign\session'])
def test_session_names_cannot_alias_another_private_path(fake_lightpanda, name):
    lp, launched = fake_lightpanda
    server, error = lp.launch_lightpanda(name)
    assert server is None and 'session name is invalid' in error and not launched


def test_same_session_name_in_an_independent_profile_never_reuses_or_closes_sibling(fake_lightpanda, native_state):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from hermes_security import vault

    lp, launched = fake_lightpanda
    home, user, _ram = native_state
    first, error = lp.launch_lightpanda('same')
    assert error is None
    sibling = user/'independent-profile'
    vault.init_vault(sibling, 'synthetic LP sibling password')
    vault.unlock(sibling, 'synthetic LP sibling password')
    token = set_hermes_home_override(sibling)
    try:
        second, error = lp.launch_lightpanda('same')
        assert error is None and second is not first and lp.get_server('same') is second
        assert first.lease.cache != second.lease.cache
        lp.stop_lightpanda('same', expected=second.lease.token)
        assert not launched[0].stopped and vault.is_unlocked(home)
    finally:
        reset_hermes_home_override(token)
    assert lp.get_server('same') is first
    lp.stop_lightpanda('same', expected=first.lease.token)


def test_unknown_cache_writer_and_repeated_cleanup_keep_parent_tracking_and_previous_cipher(fake_lightpanda, native_state, monkeypatch):
    from hermes_cli import browser_worker_store as store
    from tools import browser_tool as browser, browser_tool_lifecycle as lifecycle

    lp, launched = fake_lightpanda
    home, _user, _ram = native_state
    server, error = lp.launch_lightpanda('retained')
    assert error is None
    unknown = server.lease.resource.path/'processes'/'unknown-other'
    store._write(unknown, {'unknown':True})
    previous = (home/server.lease.resource.relative).read_bytes()
    info = {'session_name':'retained','features':{'local':True,'lightpanda':True},
            'lightpanda_epoch':server.lease.token}
    monkeypatch.setattr(browser, '_active_sessions', {'retained-task':info})
    monkeypatch.setattr(browser, '_session_owner_homes', {'retained-task':home})
    monkeypatch.setattr(browser, '_session_last_activity', {'retained-task':123})
    released = []
    monkeypatch.setattr(lifecycle, '_release_session_resources', lambda *_a: released.append(True))
    monkeypatch.setattr(lifecycle._recording, 'stop', lambda *_a: None)
    with pytest.raises(RuntimeError, match='identity is unknown'):
        lifecycle._force_reap_browser_session('retained-task')
    assert browser._active_sessions['retained-task'] is info
    assert browser._session_last_activity['retained-task'] == 123 and not released
    assert lp.get_server('retained') is server and launched[0].stopped
    assert (home/server.lease.resource.relative).read_bytes() == previous
    unknown.unlink()
    lp.stop_lightpanda('retained', expected=server.lease.token)


@pytest.mark.parametrize('explicit', [None, 'false'])
def test_vendor_telemetry_default_and_explicit_override_are_not_local_authority(native_state, explicit):
    from tools.browser_lightpanda_state import vendor_environment

    values = {'__HERMES_INSTALL_OWNER_FD':'97'}
    if explicit is not None:
        values['LIGHTPANDA_DISABLE_TELEMETRY'] = explicit
    env = vendor_environment(values)
    assert env['LIGHTPANDA_DISABLE_TELEMETRY'] == ('true' if explicit is None else explicit)
    assert '__HERMES_INSTALL_OWNER_FD' not in env


def test_resident_lifetime_does_not_inherit_the_completed_launch_request_budget(fake_lightpanda, native_state):
    from hermes_security.operation_custody import OperationBudget

    lp, launched = fake_lightpanda
    home, _user, _ram = native_state
    budget = OperationBudget(home)
    with budget.scope():
        server, error = lp.launch_lightpanda('resident')
    assert error is None
    budget.cancelled.set()
    assert server.is_alive()
    lp.stop_lightpanda('resident', expected=server.lease.token)
    assert launched[0].stopped and lp.get_server('resident') is None


def test_nested_owner_cleanup_uses_captured_source_instead_of_unrelated_ambient_session(fake_lightpanda, native_state):
    from agent.session_context import scoped_current_session_id
    from hermes_security.session_authority import start_session_authority

    lp, launched = fake_lightpanda
    home, _user, _ram = native_state
    a = start_session_authority(home, 'lp-queued-A')
    try:
        with scoped_current_session_id('lp-queued-A'):
            server, error = lp.launch_lightpanda('queued')
        assert error is None
        info = {'session_name':'queued','features':{'lightpanda':True},'lightpanda_epoch':server.lease.token}
        with lp.cleanup_scope(info, home=home, original=True):
            with scoped_current_session_id('unrelated-unbound-session'):
                with lp.cleanup_scope(info, home=home):
                    lp.stop_lightpanda('queued', expected=server.lease.token)
        assert launched[0].stopped
    finally:
        a.close()


def test_existing_owner_worker_repair_recovers_ended_A_under_fresh_B_without_reviving_A(fake_lightpanda, native_state):
    from agent.session_context import scoped_current_session_id
    from hermes_cli.browser_worker_repair import repair_owned
    from hermes_security import browser_worker_archive as archive
    from hermes_security.session_authority import start_session_authority
    from hermes_security.errors import VaultLockedError

    lp, launched = fake_lightpanda
    home, _user, ram = native_state
    a = start_session_authority(home, 'lp-repair-ended-A')
    b = start_session_authority(home, 'lp-repair-fresh-B')
    try:
        with scoped_current_session_id('lp-repair-ended-A'):
            original, error = lp.launch_lightpanda('ended-A')
        assert error is None
        payload = b'latest cached work belonging to original home'
        (original.lease.cache/'latest').write_bytes(payload)
        a.close()
        with scoped_current_session_id('lp-repair-fresh-B'):
            report = repair_owned(home)
        assert report['retained'] == [] and report['saved'] == [original.lease.resource.relative.as_posix()]
        assert launched[0].stopped and not original.lease.resource.path.exists() and b.is_live()
        with pytest.raises(VaultLockedError):
            original.lease.source_operation.check()
        target = ram/'fresh-explicit-readback'; target.mkdir(mode=0o700)
        archive.replay_archive(home, report['saved'][0], custody=archive.capture_custody(home), target=target)
        assert (target/'http-cache'/'latest').read_bytes() == payload
    finally:
        a.close(); b.close()


def test_duplicate_named_launch_refuses_before_another_producer_or_pending_marker(fake_lightpanda,native_state):
    lp,launched=fake_lightpanda
    first,error=lp.launch_lightpanda('same-name')
    assert first is not None and error is None
    before={p.name:p.read_bytes() for p in (first.lease.resource.path/'processes').iterdir()}
    second,error=lp.launch_lightpanda('same-name')
    assert second is None and error
    assert len(launched)==1,'Duplicate name must refuse before starting a second private producer'
    assert {p.name:p.read_bytes() for p in (first.lease.resource.path/'processes').iterdir()}==before
    lp.stop_lightpanda(first.session_name,expected=first.lease.token)
    assert lp.get_server(first.session_name) is None
