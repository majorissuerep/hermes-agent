"""First-party private screenshot dispatch; SDK/browser/RAM are declared unit seams."""
from contextlib import redirect_stderr, redirect_stdout
import base64
import io
import inspect
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
from types import ModuleType

import pytest

pytest_plugins = ['tests.tools.test_browser_native_private_state']
pytestmark = pytest.mark.platforms('linux', 'macos')


class DeclaredSDK:
    def __init__(self, monkeypatch, *, ready=False, failure=None):
        self.events, self.failure, self.ready = [], failure, ready
        self.identity = {'session_id': 'original-session', 'target_id': 'original-target'}
        self.path = None
        self.helpers = ModuleType('browser_harness.helpers')
        self.helpers.DEFAULT_IPC_RESPONSE_TIMEOUT_SECONDS = 5.0
        self.helpers.SCREENSHOT_IPC_RESPONSE_TIMEOUT_SECONDS = 60.0
        self.helpers._IPCResponseTimeout = TimeoutError
        self.helpers._send = self.send
        self.helpers.capture_screenshot = self.capture
        package, entry = ModuleType('browser_harness'), ModuleType('browser_harness.run')
        entry.main = self.main
        package.helpers, package.run = self.helpers, entry
        for name, value in [('browser_harness', package), ('browser_harness.helpers', self.helpers),
                            ('browser_harness.run', entry)]:
            monkeypatch.setitem(sys.modules, name, value)

    def send(self, request, response_timeout=5.0):
        kind = request.get('method') or request['meta']
        self.events.append((kind, request.get('session_id'), response_timeout))
        if kind == 'connection_status':
            if self.failure == 'missing-target':
                return {'session_id': 'original-session', 'target_id': None}
            return dict(self.identity)
        if kind == 'Page.bringToFront':
            if self.failure == 'activation':
                raise RuntimeError('declared activation refused')
            self.ready = True
            if self.failure == 'changed-target':
                self.identity['target_id'] = 'replacement-target'
            return {'result': {}}
        if kind == 'Page.captureScreenshot':
            if not self.ready:
                raise TimeoutError('declared hidden-page capture failure')
            if self.failure == 'changed-after-capture':
                self.identity['session_id'] = 'replacement-session'
            return {'result': {'data': base64.b64encode(b'declared rendered bytes').decode()}}
        raise AssertionError('Unexpected declared SDK request')

    def capture(self, path=None, full=False, max_dim=None):
        self.capture_arguments = path, full, max_dim
        selected = path or self.path
        response = self.helpers._send({'method': 'Page.captureScreenshot', 'session_id': None,
                                      'params': {'format': 'png', 'captureBeyondViewport': full}},
                                     response_timeout=60.0)
        Path(selected).write_bytes(base64.b64decode(response['result']['data']))
        return selected

    def main(self):
        print(self.helpers.capture_screenshot())


@pytest.fixture
def dispatch(native_state, monkeypatch):
    from tools import browser_use_cli as harness

    monkeypatch.setattr(harness, '_find_cli', lambda: [sys.executable, '-m', 'browser_harness.run'])
    monkeypatch.setattr(harness, '_harness_site_dir', lambda: '/declared-sdk-site')
    monkeypatch.setattr(harness, '_attach_vault_supervisor', lambda *_a: None)
    monkeypatch.setattr(harness, '_native_screenshot_result', lambda *_a: None)
    monkeypatch.setattr('tools.browser_harness_runtime._DAEMONS', {})
    monkeypatch.setattr('hermes_platform.browser_process.harness_runtime_absent', lambda *_a, **_kw: True)
    monkeypatch.setattr('hermes_platform.browser_process.harness_process_record', lambda *_a, **_kw: None)
    state = {'private': True, 'sdk': None, 'calls': []}

    def route(env, *_args, **_kwargs):
        env['BU_CDP_URL'] = 'http://declared-unit-browser'
        env[harness._PRIVATE_BROWSER_SENTINEL] = state['private']

    def vendor(command, code, env, _timeout):
        env.check(); env.starting()
        sdk = state['sdk']
        sdk.path = Path(env['BH_TMP_DIR']) / 'shot.png'
        state['calls'].append((command, code, dict(env)))
        output, errors = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(output), redirect_stderr(errors):
                if len(command) == 2:
                    runpy.run_path(command[1], run_name='__main__')
                else:
                    sdk.main()
        except SystemExit as exc:
            return subprocess.CompletedProcess(command, exc.code or 0, output.getvalue(), errors.getvalue())
        except (RuntimeError, TimeoutError) as exc:
            return subprocess.CompletedProcess(command, 1, output.getvalue(), str(exc))
        return subprocess.CompletedProcess(command, 0, output.getvalue(), errors.getvalue())

    monkeypatch.setattr(harness, '_route_backend', route)
    monkeypatch.setattr(harness, '_run_cli_killing_process_group', vendor)
    return harness, state


def test_private_screenshot_activates_only_its_original_target_before_single_capture(dispatch, monkeypatch):
    harness, state = dispatch
    sdk = state['sdk'] = DeclaredSDK(monkeypatch)
    result = json.loads(harness.browser_exec('print(capture_screenshot())', session='owned-name', task_id='task'))
    assert result['success'], result
    assert [event[0] for event in sdk.events] == ['connection_status', 'Page.bringToFront',
                                                'connection_status', 'Page.captureScreenshot', 'connection_status']
    assert [event[1] for event in sdk.events if event[0].startswith('Page.')] == ['original-session'] * 2
    assert sdk.path.read_bytes() == b'declared rendered bytes'
    assert state['calls'][0][1] == 'print(capture_screenshot())'
    assert not any(name.startswith('_HERMES_BU_') for name in state['calls'][0][2])


@pytest.mark.parametrize('failure', ['activation', 'missing-target', 'changed-target', 'changed-after-capture'])
def test_private_screenshot_refuses_failed_or_changed_activation_without_artifact(dispatch, monkeypatch, failure):
    harness, state = dispatch
    sdk = state['sdk'] = DeclaredSDK(monkeypatch, failure=failure)
    result = json.loads(harness.browser_exec('print(capture_screenshot())', session='owned-name', task_id='task'))
    assert not result['success']
    assert not sdk.path.exists()
    captures = [event for event in sdk.events if event[0] == 'Page.captureScreenshot']
    assert len(captures) == (1 if failure == 'changed-after-capture' else 0)
    assert 'screenshot_path' not in result


def test_shared_named_browser_keeps_sdk_capture_without_taking_focus(dispatch, monkeypatch):
    harness, state = dispatch
    state['private'] = False
    sdk = state['sdk'] = DeclaredSDK(monkeypatch, ready=True)
    result = json.loads(harness.browser_exec('print(capture_screenshot())', session='shared-name', task_id='task'))
    assert result['success'], result
    assert [event[0] for event in sdk.events] == ['Page.captureScreenshot']
    assert state['calls'][0][0] == [sys.executable, '-m', 'browser_harness.run']


def test_closed_source_cannot_start_capture_but_sibling_uses_shared_named_resource(dispatch, native_state, monkeypatch):
    from agent.session_context import scoped_current_session_id
    from hermes_security import vault
    from hermes_security.session_authority import start_session_authority

    harness, state = dispatch
    home, _user, _ram = native_state
    first = start_session_authority(home, 'screenshot-A')
    sibling = start_session_authority(home, 'screenshot-B')
    original_route = harness._route_backend
    def close_original(env, *args, **kwargs):
        original_route(env, *args, **kwargs)
        first.close()
    monkeypatch.setattr(harness, '_route_backend', close_original)
    try:
        with scoped_current_session_id(first.session_id):
            refused = json.loads(harness.browser_exec('print(capture_screenshot())', session='same-name', task_id='task'))
        assert 'error' in refused and state['calls'] == []
        monkeypatch.setattr(harness, '_route_backend', original_route)
        sdk = state['sdk'] = DeclaredSDK(monkeypatch)
        with scoped_current_session_id(sibling.session_id):
            own = json.loads(harness.browser_exec('print(capture_screenshot())', session='same-name', task_id='task'))
        assert own['success'] and len(state['calls']) == 1
        assert sdk.path.exists() and vault.is_unlocked(home)
    finally:
        first.close(); sibling.close()


def test_original_sdk_signature_path_flags_and_raw_cdp_are_preserved(tmp_path, monkeypatch):
    from tools.browser_harness_screenshot import install_private_capture

    sdk = DeclaredSDK(monkeypatch)
    original_signature = inspect.signature(sdk.helpers.capture_screenshot)
    install_private_capture(sdk.helpers)
    assert inspect.signature(sdk.helpers.capture_screenshot) == original_signature
    selected = tmp_path / 'selected.png'
    assert sdk.helpers.capture_screenshot(selected, full=True, max_dim=1440) is selected
    assert sdk.capture_arguments == (selected, True, 1440)
    assert selected.read_bytes() == b'declared rendered bytes'
    sdk.events.clear()
    response = sdk.helpers._send({'method': 'Page.captureScreenshot', 'session_id': 'explicit-raw-session'},
                                response_timeout=17)
    assert response['result']['data']
    assert sdk.events == [('Page.captureScreenshot', 'explicit-raw-session', 17)]


def test_capture_and_added_fences_share_the_original_sixty_second_budget():
    from tools.browser_harness_screenshot import _capture_request

    now, observed = [0.0], []
    costs = iter([2, 3, 2, 49, 1])
    def send(request, response_timeout):
        observed.append((request, response_timeout))
        now[0] += next(costs)
        return ({'session_id': 'original-session', 'target_id': 'original-target'}
                if request.get('meta') else {'result': {'data': 'declared-rendered-data'}})
    response = _capture_request(send, {'method': 'Page.captureScreenshot', 'session_id': None}, 60,
                                ordinary_timeout=5, timeout_error=TimeoutError, clock=lambda: now[0])
    assert response['result']['data'] == 'declared-rendered-data'
    assert [timeout for _request, timeout in observed] == [5, 5, 5, 53, 4]
    assert now[0] == 57
    assert [request['session_id'] for request, _timeout in observed if request.get('method')] == ['original-session'] * 2


def test_expired_total_budget_cannot_publish_a_late_capture_response():
    from tools.browser_harness_screenshot import _capture_request

    now, events = [0.0], []
    costs = iter([2, 3, 2, 54])
    def late_send(request, response_timeout):
        events.append((request.get('method') or request['meta'], response_timeout))
        now[0] += next(costs)
        return ({'session_id': 'original-session', 'target_id': 'original-target'}
                if request.get('meta') else {'result': {'data': 'must-not-be-published'}})
    with pytest.raises(TimeoutError, match='original total deadline'):
        _capture_request(late_send, {'method': 'Page.captureScreenshot'}, 60,
                         ordinary_timeout=5, timeout_error=TimeoutError, clock=lambda: now[0])
    assert events == [('connection_status', 5), ('Page.bringToFront', 5),
                      ('connection_status', 5), ('Page.captureScreenshot', 53)]
    assert now[0] == 61


def test_absolute_worker_command_tracks_a_relocated_product_and_selected_pm_interpreter(tmp_path):
    from tools import browser_harness_worker as worker

    staged = tmp_path / 'relocated product' / 'tools' / 'browser_harness_worker.py'
    staged.parent.mkdir(parents=True)
    shutil.copy2(worker.__file__, staged)
    from tools import browser_harness_screenshot
    shutil.copy2(browser_harness_screenshot.__file__, staged.with_name('browser_harness_screenshot.py'))
    namespace = runpy.run_path(str(staged), run_name='declared_staged_worker')
    original = ['/declared/PM-python', '-m', 'browser_harness.run']
    assert namespace['command_for_browser'](original, private=True) == [original[0], str(staged)]
    assert namespace['command_for_browser'](original, private=False) is original
    site = tmp_path / 'declared-site' / 'browser_harness'
    site.mkdir(parents=True)
    (site / '__init__.py').write_text('')
    (site / 'helpers.py').write_text('''
DEFAULT_IPC_RESPONSE_TIMEOUT_SECONDS = 5.0
_IPCResponseTimeout = TimeoutError
events = []
def _send(request, response_timeout=5.0):
    events.append(request.get('method') or request['meta'])
    return {'session_id':'staged-session','target_id':'staged-target'}
def capture_screenshot(path=None, full=False, max_dim=None):
    _send({'method':'Page.captureScreenshot','session_id':None}, response_timeout=60)
    return 'declared-no-image-writer'
''')
    (site / 'run.py').write_text('''
import json,sys
from . import helpers
from .helpers import capture_screenshot
def main():
    value = capture_screenshot()
    print(json.dumps({'value':value,'events':helpers.events,'python':sys.executable}))
''')
    unrelated = tmp_path / 'unrelated-cwd'
    unrelated.mkdir()
    result = subprocess.run([sys.executable, '-S', str(staged)], cwd=unrelated,
                            env={'PATH':os.environ['PATH'], 'HOME':str(tmp_path),
                                 'PYTHONPATH':str(site.parent), 'LANG':'C.UTF-8'},
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert Path(observed['python']).resolve() == Path(sys.executable).resolve()
    assert observed['value'] == 'declared-no-image-writer'
    assert observed['events'] == ['connection_status', 'Page.bringToFront', 'connection_status',
                                  'Page.captureScreenshot', 'connection_status']
