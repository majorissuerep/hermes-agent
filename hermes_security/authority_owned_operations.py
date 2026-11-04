"""Closed owner-maintenance operations in the existing native start/poll/cancel registry."""
from __future__ import annotations

import json
import secrets


def _browser_repair(home):
    from hermes_cli.management_owned_jobs import OwnedManagementJob
    return OwnedManagementJob(home, 'browser.outgoing.repair')


def _worker_repair(home):
    from hermes_cli.management_owned_jobs import OwnedManagementJob
    return OwnedManagementJob(home, 'browser.worker.repair')


_FACTORIES = {'browser.outgoing.repair': _browser_repair, 'browser.worker.repair': _worker_repair}


def selected(arguments, data):
    if arguments != ['manage', 'apply']:
        return None
    try:
        request = json.loads(data)
    except (ValueError, UnicodeError):
        return None
    if (not isinstance(request, dict) or not isinstance(request.get('op'), str)
            or request['op'] not in _FACTORIES):
        return None
    from hermes_cli.management import _request
    _request(request)  # no target/home/code/credential/timeout payload fields
    return request['op']


def start(authority, operation, jobs, lock):
    """Capture exact policy outside the registry lock; register before effects can start."""
    from contextlib import ExitStack
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from agent.secret_scope import build_profile_secret_scope, set_secret_scope, reset_secret_scope
    from agent.session_context import scoped_current_session_id
    from tools.terminal_scope import install_profile_terminal_scope, reset_terminal_scope
    import time

    admitted_at = time.monotonic()
    with ExitStack() as stack:
        home_token = set_hermes_home_override(authority.home)
        stack.callback(reset_hermes_home_override, home_token)
        stack.enter_context(scoped_current_session_id(authority.session_id))
        secret_token = set_secret_scope(build_profile_secret_scope(authority.home), profile_home=str(authority.home))
        stack.callback(reset_secret_scope, secret_token)
        terminal_token = install_profile_terminal_scope(authority.home)
        stack.callback(reset_terminal_scope, terminal_token)
        child = _FACTORIES[operation](authority.home)  # custody/RPC/config reads, but no thread/effect yet
        with lock:
            if not authority.is_live() or time.monotonic() - admitted_at >= 3:
                raise ValueError('owning operation admission expired before effects could start')
            for identity, (owner, previous) in list(jobs.items()):
                if owner is authority and previous.finished.is_set() and time.monotonic() - previous.last_poll >= 20:
                    del jobs[identity]
            if sum(owner is authority for owner, _ in jobs.values()) >= 4:
                raise ValueError('the owning session already has four native commands')
            identity = secrets.token_hex(24)
            jobs[identity] = authority, child
            try:
                child.start()
            except BaseException:
                del jobs[identity]
                raise
    return {'ok': True, 'job': identity}
