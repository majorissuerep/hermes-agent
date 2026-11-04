"""Cold named-state contracts at the real native adapter, with an explicit fake vendor.

Only the daemon identity is a real owned Python process. Browser behavior and RAM proof are
declared unit seams; normal-sandbox Chromium cold-state qualification runs in disposable VMs.
"""
from pathlib import Path
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

pytest_plugins = ['tests.tools.test_browser_native_private_state']
pytestmark = pytest.mark.platforms('linux', 'macos')


class NamedVendor:
    def __init__(self, payload, *, fail_load=False, replace_on_load=False):
        self.payload, self.fail_load = payload, fail_load
        self.replace_on_load = replace_on_load
        self.calls, self.children = [], []
        self.loaded = False
        self.selected = None
        self.input = None

    def __call__(self, argv, *, env, **_kwargs):
        # This fake implements only the fixture's fixed executable/session/JSON prefix.
        command = SimpleNamespace(action=argv[4], arguments=tuple(argv[5:]))
        self.calls.append((command.action, command.arguments))
        if command.action == 'get':
            self.start(env)
            self.selected.write_bytes(b'vendor bootstrap replaced its selected pathname')
        if command.action == 'state':
            assert command.arguments[0] == 'load'
            self.input = Path(command.arguments[1])
            assert self.input != self.selected and self.input.read_bytes() == self.payload
            if self.fail_load:
                return 1, b'{"success":false}', b'synthetic private bootstrap refusal'
            if self.replace_on_load:
                self.stop()
                self.start(env)
            self.loaded = True
        result = {'success':True, 'restored':self.loaded}
        return 0, json.dumps(result).encode(), b''

    def start(self, env):
        child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read(1)'],
                                 env=dict(env), stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.children.append((child, Path(env['AGENT_BROWSER_SOCKET_DIR'])/(env.lease.session+'.pid')))
        self.children[-1][1].write_text(str(child.pid))

    def stop(self):
        for child, pidfile in self.children:
            if child.poll() is None:
                child.communicate(b'x', timeout=10)
            pidfile.unlink(missing_ok=True)


def _prepared(home, vendor, task):
    from hermes_cli import browser_worker_store as store
    from tools import browser_tool_session as session

    resource = store.resource(home, kind='native-browser-config')
    with resource.operation():
        directory = resource.tree
        for name in ('home', '.agent-browser', 'sessions'):
            directory = directory/name
            directory.mkdir(mode=0o700)
        vendor.selected = directory/'private-name-original.json'
        vendor.selected.write_bytes(vendor.payload)
    socket = session._prepare_session_socket_dir(task)
    env = session._agent_browser_command_env(socket)
    env['AGENT_BROWSER_SESSION_NAME'] = 'private-name'
    argv = [sys.executable, '--session', task, '--json', 'open', 'http://synthetic.invalid/cold']
    return resource, socket, env, argv


def test_fresh_named_daemon_loads_exact_prebootstrap_input_once_and_keeps_original_source(native_state, monkeypatch):
    from agent.session_context import scoped_current_session_id
    from hermes_cli import browser_worker_store as store
    from hermes_security import vault
    from hermes_security.errors import VaultLockedError
    from hermes_security.session_authority import start_session_authority
    from tools import browser_native_runtime, browser_tool_session as session

    home, _user, _ram = native_state
    vendor = NamedVendor(b'{"origins":[{"private":"original sole-copy state"}]}')
    monkeypatch.setattr('hermes_platform.client_capture.run_client', vendor)
    first = start_session_authority(home, 'native-load-A')
    sibling = start_session_authority(home, 'native-load-B')
    try:
        with scoped_current_session_id('native-load-A'):
            resource, _socket, env, argv = _prepared(home, vendor, 'named-original-owner')
            result = session._run_agent_browser_client(argv, env, None, 20)
            assert result[0] == 0 and json.loads(result[1])['restored'] is True
            assert [action for action, _args in vendor.calls] == ['get', 'state', 'open']
            assert vendor.input.read_bytes() == vendor.payload and vendor.selected.read_bytes() != vendor.payload
            session._run_agent_browser_client(argv, env, None, 20)
            assert [action for action, _args in vendor.calls] == ['get', 'state', 'open', 'open']
            assert any(path.read_bytes() == vendor.payload for path in vendor.selected.parent.glob('private-name-*.json'))
        first.close()
        before = len(vendor.calls)
        with scoped_current_session_id('native-load-B'):
            with pytest.raises(VaultLockedError):
                session._run_agent_browser_client(argv, env.copy(), None, 20)
            own = session._agent_browser_command_env(str(env.lease.socket))
            own['AGENT_BROWSER_SESSION_NAME'] = 'private-name'
            assert session._run_agent_browser_client(argv, own, None, 20)[0] == 0
            assert len(vendor.calls) == before+1
            vendor.stop()
            browser_native_runtime.stop_and_release('named-original-owner', str(env.lease.socket))
            assert store.retire_quiescent(home)['archived'] and not resource.path.exists()
            store._POOL.clear()
            vendor.loaded = False
            cold_socket = session._prepare_session_socket_dir('named-original-owner')
            cold = session._agent_browser_command_env(cold_socket)
            cold['AGENT_BROWSER_SESSION_NAME'] = 'private-name'
            result = session._run_agent_browser_client(argv, cold, None, 20)
            assert json.loads(result[1])['restored'] is True
        assert len(vendor.calls) == before+4 and sibling.is_live() and vault.is_unlocked(home)
    finally:
        vendor.stop()
        first.close()
        sibling.close()


@pytest.mark.parametrize('failure', ['refused-load', 'replaced-daemon'])
def test_failed_explicit_load_preserves_cipher_and_input_until_fresh_owner_authorizes_retry(native_state, monkeypatch, failure):
    from hermes_cli import browser_native_restore as recovery
    from hermes_cli import browser_worker_store as store
    from hermes_cli.browser_worker_repair import repair_owned
    from hermes_security.operation_custody import OperationBudget
    from tools import browser_native_runtime, browser_tool_session as session

    home, _user, _ram = native_state
    vendor = NamedVendor(b'{"origins":[{"private":"retained state after load refusal"}]}',
                         fail_load=failure == 'refused-load', replace_on_load=failure == 'replaced-daemon')
    monkeypatch.setattr('hermes_platform.client_capture.run_client', vendor)
    resource, socket, env, argv = _prepared(home, vendor, 'named-refused-owner')
    before = (home/resource.relative).read_bytes()
    try:
        with pytest.raises(store.UnverifiedWorkerState, match='browser.worker.repair'):
            session._run_agent_browser_client(argv, env, None, 20)
        assert [action for action, _args in vendor.calls] == ['get', 'state']
        assert (home/resource.relative).read_bytes() == before
        assert vendor.input.read_bytes() == vendor.payload
        with pytest.raises(store.UnverifiedWorkerState):
            session._run_agent_browser_client(argv, env, None, 20)
        assert len(vendor.calls) == 2
        vendor.stop()
        browser_native_runtime.stop_and_release('named-refused-owner', socket)
        with OperationBudget(home).scope():
            report = repair_owned(home)
        assert report['saved'] == [] and len(report['retained']) == 1
        assert 'Retry the original browser command' in report['retained'][0]
        assert (home/resource.relative).read_bytes() == before
        assert recovery.status(resource)['retry'] is True
        vendor.fail_load = False
        vendor.replace_on_load = False
        socket = session._prepare_session_socket_dir('named-refused-owner')
        env = session._agent_browser_command_env(socket)
        env['AGENT_BROWSER_SESSION_NAME'] = 'private-name'
        assert json.loads(session._run_agent_browser_client(argv, env, None, 20)[1])['restored'] is True
        assert vendor.input.read_bytes() == vendor.payload and recovery.status(resource) is None
        assert (home/resource.relative).read_bytes() != before
    finally:
        vendor.stop()


def test_unknown_legacy_snapshot_order_refuses_before_launch_and_retains_every_copy(native_state, monkeypatch):
    from hermes_cli import browser_worker_store as store
    from tools import browser_tool_session as session

    home, _user, _ram = native_state
    vendor = NamedVendor(b'original legacy saved state')
    monkeypatch.setattr('hermes_platform.client_capture.run_client', vendor)
    resource, _socket, env, argv = _prepared(home, vendor, 'ambiguous-original-owner')
    alternative = vendor.selected.parent/'private-name-other.json'
    with resource.operation():
        alternative.write_bytes(b'another legacy saved state with unknown chronology')
    before = (home/resource.relative).read_bytes()
    with pytest.raises(store.UnverifiedWorkerState, match='chronology is ambiguous'):
        session._run_agent_browser_client(argv, env, None, 20)
    assert vendor.calls == [] and not list((resource.path/'processes').iterdir())
    assert (home/resource.relative).read_bytes() == before
    assert vendor.selected.read_bytes() == vendor.payload
    assert alternative.read_bytes() == b'another legacy saved state with unknown chronology'
