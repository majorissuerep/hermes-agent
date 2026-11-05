"""The real dispatcher keeps one daemon target while task workspaces remain separate.

Browser execution and RAM backing are declared unit seams, not native browser evidence.
"""
from pathlib import Path
import json
import subprocess

import pytest

pytest_plugins = ['tests.tools.test_browser_native_private_state']
pytestmark = pytest.mark.platforms('linux', 'macos')


@pytest.mark.parametrize(('inherited', 'session', 'expected'), [
    (None, '', 'default'),
    ('operator-selected', '', 'operator-selected'),
    ('operator-selected', 'explicit-session', 'explicit-session'),
])
def test_effective_daemon_target_survives_task_switch_without_workspace_sharing(
        native_state, monkeypatch, inherited, session, expected):
    from tools import browser_use_cli as harness

    _home, _user, _ram = native_state
    base = harness._base_subprocess_env
    monkeypatch.setattr(harness, '_base_subprocess_env',
                        lambda: dict(base(), **({'BU_NAME': inherited} if inherited else {})))
    monkeypatch.setattr(harness, '_find_cli', lambda: ['declared-unit-harness'])
    monkeypatch.setattr(harness, '_attach_vault_supervisor', lambda *_a: None)
    monkeypatch.setattr(harness, '_resolve_real_profile_cdp', lambda *_a, **_kw: None)
    monkeypatch.setattr('hermes_platform.browser_process.harness_runtime_absent', lambda *_a, **_kw: True)
    monkeypatch.setattr('hermes_platform.browser_process.harness_process_record', lambda *_a, **_kw: None)
    routes, effects = [], []
    changed_target = False

    def browser(env, task_id, *, session_name, **_kw):
        key = harness._backend_cache_key(task_id, session_name)
        routes.append((session_name, key))
        env['BU_CDP_URL'] = 'http://declared-unit-browser/' + key + ('-replaced' if changed_target else '')

    def vendor(cmd, _code, env, _timeout):
        env.check()
        env.starting()
        workspace = Path(env['BH_AGENT_WORKSPACE'])
        state = workspace/'sole-copy.tmp'
        if state.exists():
            assert state.read_bytes() == b'original task progress'
        else:
            state.write_bytes(b'original task progress')
        effects.append((env['BU_NAME'], workspace))
        return subprocess.CompletedProcess(cmd, 0, 'original task progress', '')

    monkeypatch.setattr(harness, '_resolve_backend_cdp', browser)
    monkeypatch.setattr(harness, '_run_cli_killing_process_group', vendor)
    for task in ('task-A', 'task-B', 'task-A'):
        result = json.loads(harness.browser_exec('print("preserved")', task_id=task, session=session))
        assert result.get('success') is True, result
    assert [name for name, _key in routes] == [expected] * 3
    assert len({key for _name, key in routes}) == 1
    assert [name for name, _path in effects] == [expected] * 3
    assert effects[0][1] == effects[2][1] != effects[1][1]
    original = {path:(path/'sole-copy.tmp').read_bytes() for _name, path in effects}
    changed_target = True
    refused = json.loads(harness.browser_exec('print("must not dispatch")', task_id='task-A', session=session))
    assert 'Existing named Browser Use target differs' in refused['error']
    assert len(effects) == 3
    assert all((path/'sole-copy.tmp').read_bytes() == payload for path, payload in original.items())
