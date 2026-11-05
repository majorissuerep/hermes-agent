"""One explicit public state load before the first command of a fresh named local daemon."""
from __future__ import annotations

import json
import time

from hermes_cli import browser_native_restore as recovery, browser_native_state as state
from hermes_cli import browser_worker_store as store
from hermes_platform.browser_process import parse_agent_browser_command, browser_process_record

_BROWSER_COMMANDS = {'open','get','eval','snapshot','screenshot','click','dblclick','hover','check',
                     'uncheck','select','fill','scroll','back','press','console','errors','record'}


def _eligible(env, command):
    attached = '--cdp' in command.prefix or any(env.get(name) for name in (
        'AGENT_BROWSER_CDP','AGENT_BROWSER_AUTO_CONNECT','AGENT_BROWSER_PROVIDER','AGENT_BROWSER_PROFILE','AGENT_BROWSER_STATE'))
    engine = env.get('AGENT_BROWSER_ENGINE', 'chrome')
    return (command.action in _BROWSER_COMMANDS and not attached
            and engine in ('auto','chrome','chromium'))


def _current(env):
    from tools.browser_tool_session import _read_browser_daemon_pid

    lease = env.lease
    pid = _read_browser_daemon_pid(str(lease.socket), lease.session)
    return browser_process_record(pid, executable=lease.executable, socket_dir=lease.socket)


def _fresh(env):
    lease = env.lease
    if _current(env) is not None:
        return False
    for suffix in ('.pid','.sock'):
        try:
            (lease.socket/(lease.session+suffix)).lstat()
        except FileNotFoundError:
            continue
        raise store.UnverifiedWorkerState('Native daemon identity is unknown; original state retained; close its owners and run browser.worker.repair')
    return True


def _success(stdout):
    try:
        result = json.loads(stdout.decode('utf-8-sig'))
    except (ValueError, UnicodeError):
        return False
    return isinstance(result, dict) and result.get('success') is True


def run(argv, env, payload, timeout, run_client):
    """Return the original client's exact bytes; bootstrap output never becomes model output."""
    from tools.browser_native_runtime import NativeEnvironment, _record_daemon, starting_client

    deadline = time.monotonic()+timeout
    def invoke(command, data=None, expected=None):
        if isinstance(env, NativeEnvironment):
            env.check()
            if expected is not None and _current(env) != expected:
                raise store.UnverifiedWorkerState('The bootstrapped native daemon changed before the requested command')
            starting_client(env)
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise store.UnverifiedWorkerState('Native bootstrap exhausted the original command deadline; requested command was not run')
        result = run_client(command, env=env, payload=data, timeout=remaining,
                            check_alive=env.check if isinstance(env, NativeEnvironment) else None)
        if isinstance(env, NativeEnvironment):
            _record_daemon(env)
        return result
    if not isinstance(env, NativeEnvironment):
        return invoke(argv, payload)
    resource, custody = env.lease.resource, env.call
    failed = recovery.status(resource)
    if failed is not None and not failed['retry']:
        raise store.UnverifiedWorkerState(recovery.message(resource, failed))
    name = env.get('AGENT_BROWSER_SESSION_NAME', '')
    command = parse_agent_browser_command(argv)
    if not name or command is None or not _eligible(env, command):
        if failed is not None:
            raise store.UnverifiedWorkerState(recovery.message(resource, failed))
        return invoke(argv, payload)
    if not recovery.valid_name(name):
        raise store.UnverifiedWorkerState('Native saved-session name is invalid; original files retained')
    if not _fresh(env):
        if failed is not None:
            raise store.UnverifiedWorkerState(recovery.message(resource, failed))
        return invoke(argv, payload)
    if failed is not None:
        if failed['name'] != name:
            raise store.UnverifiedWorkerState(recovery.message(resource, failed))
        recovery.verify_input(resource, failed, custody)
        candidate = recovery.named_copy(resource, failed, custody)
        document = recovery.phase(resource, failed, 'bootstrap', custody)
    else:
        selected = state.selected(resource, name, custody)
        if selected is None:
            return invoke(argv, payload)
        candidate, fingerprint = selected
        document = recovery.snapshot(resource, candidate, fingerprint, name, custody)
    recovery.newest_runtime_candidate(resource, candidate, custody)
    immutable = recovery.verify_input(resource, document, custody)
    prefix = command.prefix
    code, stdout, stderr = invoke([*prefix, 'get','cdp-url'])
    if code != 0 or not _success(stdout):
        recovery.refusal(resource, document, code, stdout, stderr, custody)
    expected = _current(env)
    if expected is None:
        raise store.UnverifiedWorkerState('Native bootstrap has no verified daemon identity; immutable input and archive retained')
    document = recovery.phase(resource, document, 'load', custody)
    recovery.verify_input(resource, document, custody)
    code, stdout, stderr = invoke([*prefix, 'state','load',str(immutable)], expected=expected)
    if code != 0 or not _success(stdout):
        recovery.refusal(resource, document, code, stdout, stderr, custody)
    if _current(env) != expected:
        raise store.UnverifiedWorkerState('Native daemon changed during state load. '+recovery.message(resource, document))
    retained = recovery.named_copy(resource, document, custody)
    recovery.complete(resource, document, retained, custody)
    return invoke(argv, payload, expected=expected)
