"""Actual Browser Use dispatcher with a declared vendor/RAM seam; no browser is launched."""
from pathlib import Path
import json
import subprocess

import pytest

pytest_plugins = ['tests.tools.test_browser_native_private_state']
pytestmark = pytest.mark.platforms('linux', 'macos')


@pytest.fixture(autouse=True)
def declared_vendor_process_seam(monkeypatch):
    # These contracts execute the dispatcher with an inert vendor, not a real harness process.
    monkeypatch.setattr('hermes_platform.browser_process.harness_runtime_absent', lambda *_a, **_kw: True)
    monkeypatch.setattr('hermes_platform.browser_process.harness_process_record', lambda *_a, **_kw: None)


def test_selected_owned_ram_subdirectory_and_shared_explicit_roles_keep_their_actual_paths(native_state):
    from tools.browser_harness_runtime import HarnessRequest

    home, user, ram = native_state
    first = HarnessRequest(home, 'returned-workspace', {'HOME':str(user)})
    first.configure('returned-root')
    with first.execution():
        nested = first.workspace_path/'nested'
        nested.mkdir(mode=0o700)
        (nested/'state').write_bytes(b'original selected nested workspace')
    second = HarnessRequest(home, 'another-task', {'HOME':str(user), 'BH_AGENT_WORKSPACE':str(nested)})
    env = second.configure('selected-subdirectory')
    with second.execution():
        assert second.workspace.path == first.workspace.path
        assert second.workspace.lock_path == first.workspace.lock_path
        assert env['BH_AGENT_WORKSPACE'] == str(nested)
        assert (nested/'state').read_bytes() == b'original selected nested workspace'
    explicit = ram/'shared-explicit-roles'; explicit.mkdir(mode=0o700)
    third = HarnessRequest(home, 'shared-selected-path', {
        'HOME':str(user), 'BH_AGENT_WORKSPACE':str(explicit), 'BH_HOME':str(explicit),
        'BH_CONFIG_DIR':str(explicit)})
    env = third.configure('shared-explicit-roles')
    with third.execution():
        assert env['BH_AGENT_WORKSPACE'] == env['BH_HOME'] == env['BH_CONFIG_DIR'] == str(explicit)
        (explicit/'auth-and-workspace.tmp').write_bytes(b'all explicitly shared roles stay together')
    assert b'all explicitly shared roles stay together' not in (home/third.workspace.relative).read_bytes()


def test_queued_environment_cannot_restart_a_named_operation_after_explicit_close(native_state):
    from hermes_cli.browser_worker_store import WorkerStateError
    from tools.browser_harness_runtime import HarnessRequest

    home, user, _ram = native_state
    request = HarnessRequest(home, 'queued-workspace', {'HOME':str(user)})
    env = request.configure('queued-close')
    with request.execution():
        queued = env.copy()
        request.daemon.stop([], lambda *_a: pytest.fail('No actual daemon was started'), original=False)
        with pytest.raises(WorkerStateError, match='closed'):
            queued.starting()
    fresh = HarnessRequest(home, 'queued-workspace', {'HOME':str(user)})
    fresh.configure('queued-close')
    with fresh.execution():
        assert fresh.daemon is not request.daemon


def test_explicit_worker_repair_resolves_only_proven_absent_harness_after_owners_close(native_state, monkeypatch):
    from hermes_cli import browser_worker_store as store
    from hermes_security import browser_worker_archive as archive
    from tools.browser_harness_runtime import HarnessRequest

    home, user, ram = native_state
    request = HarnessRequest(home, 'failed-launch-retained', {'HOME':str(user)})
    env = request.configure('failed-launch-retained')
    with request.execution():
        env.starting()
        (request.workspace_path/'sole-copy.tmp').write_bytes(b'complete retained workspace after failed launch')
        monkeypatch.setattr('hermes_platform.browser_process.harness_runtime_absent', lambda *_a, **_kw: False)
    request.workspace.release()
    request.config.release()
    originals = {selected.relative:(home/selected.relative).read_bytes()
                 for selected in (request.config, request.workspace)}
    refused = store.retire_quiescent(home)
    assert refused['retained'] and not refused['archived']
    assert all((home/path).read_bytes() == cipher for path, cipher in originals.items())
    assert (request.workspace_path/'sole-copy.tmp').read_bytes() == b'complete retained workspace after failed launch'
    monkeypatch.setattr('hermes_platform.browser_process.harness_runtime_absent', lambda *_a, **_kw: True)
    repaired = store.retire_quiescent(home)
    assert not repaired['retained'] and set(repaired['archived']) == {path.as_posix() for path in originals}
    assert not request.workspace.path.exists() and not request.config.path.exists()
    verified = ram/'explicit-repair-full-readback'; verified.mkdir(mode=0o700)
    archive.replay_archive(home, request.workspace.relative, custody=request.workspace.custody, target=verified)
    assert (verified/'sole-copy.tmp').read_bytes() == b'complete retained workspace after failed launch'


def test_browser_use_default_workspace_config_and_timeout_progress_stay_private(native_state, monkeypatch):
    from tools import browser_use_cli as harness

    home, user, ram = native_state
    observed = []
    payload = b'synthetic private Browser Use workspace and auth'
    monkeypatch.setattr(harness, '_find_cli', lambda: ['declared-unit-harness'])
    monkeypatch.setattr(harness, '_route_backend', lambda *_a, **_kw: None)
    monkeypatch.setattr(harness, '_attach_vault_supervisor', lambda *_a: None)
    def vendor(cmd, _code, env, timeout):
        env.check()
        env.starting()
        observed.append(dict(env))
        workspace = Path(env['BH_AGENT_WORKSPACE'])
        workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
        if len(observed) == 1:
            (workspace/'agent_helpers.py').write_bytes(payload)
            config = Path(env.get('BH_CONFIG_DIR', user/'.config'/'browser-harness'))
            config.mkdir(mode=0o700, parents=True, exist_ok=True)
            (config/'auth.json').write_bytes(payload)
            raise subprocess.TimeoutExpired(cmd, timeout)
        assert (workspace/'agent_helpers.py').read_bytes() == payload
        return subprocess.CompletedProcess(cmd, 0, 'preserved private progress', '')
    monkeypatch.setattr(harness, '_run_cli_killing_process_group', vendor)
    first = json.loads(harness.browser_exec('print("first")', task_id='private-task'))
    assert 'timed out' in first['error']
    second = json.loads(harness.browser_exec('print("second")', task_id='private-task'))
    assert second['success'] and second['output'] == 'preserved private progress'
    assert observed[0]['BH_AGENT_WORKSPACE'] == observed[1]['BH_AGENT_WORKSPACE']
    for env in observed:
        for name in ('BH_AGENT_WORKSPACE', 'BH_CONFIG_DIR', 'BH_HOME', 'BH_RUNTIME_DIR', 'BH_TMP_DIR', 'TMPDIR'):
            assert Path(env[name]).is_relative_to(ram)
    assert not any(payload in path.read_bytes() for root in (home,user) for path in root.rglob('*') if path.is_file())
    assert len(list((home/'browser-worker').glob('*.hbwa'))) >= 2


def test_browser_use_original_request_and_shutdown_cannot_borrow_a_later_sibling(native_state, monkeypatch):
    from agent.session_context import scoped_current_session_id
    from hermes_security import vault
    from hermes_security.session_authority import start_session_authority
    from tools import browser_use_cli as harness

    home, _user, _ram = native_state
    first = start_session_authority(home, 'harness-original-A')
    sibling = start_session_authority(home, 'harness-current-B')
    calls = []
    monkeypatch.setattr(harness, '_find_cli', lambda: ['declared-unit-harness'])
    monkeypatch.setattr(harness, '_route_backend', lambda *_a, **_kw: None)
    monkeypatch.setattr(harness, '_attach_vault_supervisor', lambda *_a: None)
    def vendor(cmd, _code, env, _timeout):
        env.check()
        env.starting()
        calls.append(dict(env))
        workspace = Path(env['BH_AGENT_WORKSPACE'])
        (workspace/'progress').write_bytes(b'RAM state retained after source revocation')
        if len(calls) == 1:
            first.close()
        return subprocess.CompletedProcess(cmd, 0, 'original private response', '')
    monkeypatch.setattr(harness, '_run_cli_killing_process_group', vendor)
    try:
        with scoped_current_session_id('harness-original-A'):
            refused = json.loads(harness.browser_exec('print("original")', session='shared-name', task_id='shared-task'))
        assert 'error' in refused and 'original private response' not in json.dumps(refused)
        with scoped_current_session_id('harness-current-B'):
            harness.stop_harness_daemons(original=True)
            assert len(calls) == 1
            own = json.loads(harness.browser_exec('print("new")', session='shared-name', task_id='shared-task'))
            assert own['success']
        assert len(calls) == 2 and vault.is_unlocked(home)
        assert calls[0]['BH_AGENT_WORKSPACE'] == calls[1]['BH_AGENT_WORKSPACE']
        assert Path(calls[0]['BH_AGENT_WORKSPACE'], 'progress').read_bytes() == b'RAM state retained after source revocation'
    finally:
        first.close()
        sibling.close()


@pytest.mark.parametrize('task', ['a/b', None])
def test_deliberate_import_is_used_by_actual_workspace_consumer_without_task_collision(native_state, monkeypatch, task):
    from hermes_cli import browser_use_import as imports
    from tools import browser_use_cli as harness

    home, _user, _ram = native_state
    name = 'a_b' if task else 'default'
    source = home/'cache'/'browser-use'/'workspace'/name
    source.mkdir(mode=0o700, parents=True)
    (source/'agent_helpers.py').write_bytes(b'def complete(): return 37\n')
    (source/'CacheStorage.tmp').write_bytes(b'complete preserved legacy progress')
    monkeypatch.setattr(imports, 'require_harness_source_idle', lambda _source, **_kw: None)
    imports.import_source(home, source, kind='workspace', task=task)
    observed = []
    monkeypatch.setattr(harness, '_find_cli', lambda: ['declared-unit-harness'])
    monkeypatch.setattr(harness, '_route_backend', lambda *_a, **_kw: None)
    monkeypatch.setattr(harness, '_attach_vault_supervisor', lambda *_a: None)
    def vendor(cmd, _code, env, _timeout):
        env.check();env.starting()
        observed.append(env['BH_AGENT_WORKSPACE'])
        workspace = Path(env['BH_AGENT_WORKSPACE'])
        assert (workspace/'agent_helpers.py').read_bytes() == b'def complete(): return 37\n'
        assert (workspace/'CacheStorage.tmp').read_bytes() == b'complete preserved legacy progress'
        return subprocess.CompletedProcess(cmd, 0, 'complete original progress', '')
    monkeypatch.setattr(harness, '_run_cli_killing_process_group', vendor)
    response = json.loads(harness.browser_exec('print("reuse")', task_id=task))
    assert response['success'] and len(observed) == 1 and response['workspace'] == observed[0]
    assert not source.exists()
    if task is not None:
        refused = json.loads(harness.browser_exec('print("collision")', task_id='a_b'))
        assert 'error' in refused and len(observed) == 1
        assert (Path(observed[0])/'CacheStorage.tmp').read_bytes() == b'complete preserved legacy progress'


@pytest.mark.parametrize('layout', ['distinct', 'nested'])
def test_explicit_config_roots_keep_their_own_data_after_deliberate_import(native_state, monkeypatch, layout):
    from hermes_cli import browser_use_import as imports
    from tools import browser_use_cli as harness

    home, user, ram = native_state
    original_home = user/'chosen-harness-home';original_home.mkdir(mode=0o700)
    original_config = original_home/'settings' if layout == 'nested' else user/'chosen-auth-config'
    original_config.mkdir(mode=0o700)
    (original_home/'home-state.tmp').write_bytes(b'original distinct HOME application data')
    (original_config/'auth.json').write_bytes(b'original selected config credential fixture')
    monkeypatch.setattr(imports, 'require_harness_source_idle', lambda _source, **_kw: None)
    imports.import_source(home, original_home, kind='config')
    if layout == 'distinct':
        imports.import_source(home, original_config, kind='config')
    base = harness._base_subprocess_env
    monkeypatch.setattr(harness, '_base_subprocess_env',
                        lambda: dict(base(), BH_HOME=str(original_home), BH_CONFIG_DIR=str(original_config)))
    monkeypatch.setattr(harness, '_find_cli', lambda: ['declared-unit-harness'])
    monkeypatch.setattr(harness, '_route_backend', lambda *_a, **_kw: None)
    monkeypatch.setattr(harness, '_attach_vault_supervisor', lambda *_a: None)
    def vendor(cmd, _code, env, _timeout):
        env.check();env.starting()
        selected_home, selected_config = Path(env['BH_HOME']), Path(env['BH_CONFIG_DIR'])
        assert selected_home != selected_config
        assert selected_home.is_relative_to(ram) and selected_config.is_relative_to(ram)
        assert (selected_home/'home-state.tmp').read_bytes() == b'original distinct HOME application data'
        assert (selected_config/'auth.json').read_bytes() == b'original selected config credential fixture'
        if layout == 'nested':assert selected_config == selected_home/'settings'
        (selected_config/'auth.json').write_bytes(b'updated selected config credential fixture')
        return subprocess.CompletedProcess(cmd, 0, 'selected config retained', '')
    monkeypatch.setattr(harness, '_run_cli_killing_process_group', vendor)
    response = json.loads(harness.browser_exec('print("selected")', task_id='config-selection'))
    assert response['success']
    assert not original_home.exists() and not original_config.exists()
    assert not any(b'updated selected config credential fixture' in path.read_bytes()
                   for path in home.rglob('*') if path.is_file())


def test_actual_ram_runtime_paths_name_and_pure_python_cleanup_are_preserved(native_state, monkeypatch):
    from tools import browser_use_cli as harness
    from hermes_cli import browser_worker_store as store

    home, _user, ram = native_state
    runtime, logs = ram/'explicit-ipc', ram/'explicit-logs'
    runtime.mkdir(mode=0o700); logs.mkdir(mode=0o700)
    base = harness._base_subprocess_env
    monkeypatch.setattr(harness, '_base_subprocess_env', lambda: dict(base(), BU_NAME='operator_name',
                        BH_RUNTIME_DIR=str(runtime), BH_TMP_DIR=str(logs), BH_RUNTIME_DIR_SHARED='1', BH_TMP_DIR_SHARED='1'))
    monkeypatch.setattr(harness, '_find_cli', lambda: ['declared-unit-harness'])
    monkeypatch.setattr(harness, '_route_backend', lambda *_a, **_kw: None)
    monkeypatch.setattr(harness, '_attach_vault_supervisor', lambda *_a: None)
    calls = []
    def vendor(cmd, _code, env, _timeout):
        env.check();env.starting();calls.append(tuple(cmd))
        assert env['BU_NAME'] == 'operator_name'
        assert Path(env['BH_RUNTIME_DIR']) == runtime and Path(env['BH_TMP_DIR']) == logs
        assert env['BH_RUNTIME_DIR_SHARED'] == env['BH_TMP_DIR_SHARED'] == '1'
        (logs/'private.log').write_bytes(b'private volatile fixture log')
        return subprocess.CompletedProcess(cmd, 0, 'pure Python complete', '')
    monkeypatch.setattr(harness, '_run_cli_killing_process_group', vendor)
    assert json.loads(harness.browser_exec('print("pure")', task_id='runtime-roots'))['success']
    harness.stop_harness_daemons()
    assert len(calls) == 1, 'A never-started daemon must not run a reload/helper process'
    report = store.retire_quiescent(home)
    assert report['archived'] and not report['retained']
    assert (logs/'private.log').read_bytes() == b'private volatile fixture log'
    from hermes_security import vault
    foreign = _user/'another-profile'; foreign.mkdir(mode=0o700)
    vault.init_vault(foreign, 'synthetic foreign runtime fixture')
    vault.unlock(foreign, 'synthetic foreign runtime fixture')
    monkeypatch.setenv('HERMES_HOME', str(foreign))
    assert 'error' in json.loads(harness.browser_exec('print("foreign")', task_id='foreign-runtime'))
    assert len(calls) == 1 and vault.is_unlocked(home) and vault.is_unlocked(foreign)
    assert (logs/'private.log').read_bytes() == b'private volatile fixture log'


def test_runtime_ssd_and_untracked_roots_refuse_without_launch_or_mutation(native_state, monkeypatch):
    from tools import browser_use_cli as harness

    _home, user, ram = native_state
    disk = user/'operator-log-target';disk.mkdir(mode=0o700)
    (disk/'only-copy').write_bytes(b'original operator data')
    base = harness._base_subprocess_env
    monkeypatch.setattr(harness, '_base_subprocess_env', lambda: dict(base(), BH_TMP_DIR=str(disk)))
    monkeypatch.setattr(harness, '_find_cli', lambda: ['declared-unit-harness'])
    def forbidden(*_a, **_kw):
        raise AssertionError('Unsafe runtime was silently ignored or reached a vendor')
    monkeypatch.setattr(harness, '_run_cli_killing_process_group', forbidden)
    assert 'error' in json.loads(harness.browser_exec('print("private")', task_id='refuse-ssd-runtime'))
    assert (disk/'only-copy').read_bytes() == b'original operator data'
    # An untracked private RAM path is retained rather than adopted as a live IPC endpoint.
    unknown = ram/'untracked-runtime';unknown.mkdir(mode=0o700)
    (unknown/'bu.pid').write_text('37')
    monkeypatch.setattr(harness, '_base_subprocess_env', lambda: dict(base(), BH_RUNTIME_DIR=str(unknown)))
    assert 'error' in json.loads(harness.browser_exec('print("private")', task_id='refuse-untracked-runtime'))
    assert (unknown/'bu.pid').read_text() == '37'
