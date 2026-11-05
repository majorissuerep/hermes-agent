"""Closed owners release confirmed native cleanup without losing live results."""
import base64
import errno
import os
import subprocess
from types import SimpleNamespace

import pytest

from hermes_platform.authority_children import NativeChild
from hermes_security import authority_native as native
from hermes_security import session_authority as sessions
from tests.hermes_platform.test_native_cleanup_confirmation import inert_child


def _owner(home, session_id):
    owner = sessions.SessionAuthority.__new__(sessions.SessionAuthority)
    owner.home, owner.session_id, owner.references = home, session_id, 1
    owner.parent = owner.directory = None
    owner.server = SimpleNamespace(shutdown=lambda: None, server_close=lambda: None)
    owner.thread = SimpleNamespace(join=lambda timeout: None)
    return owner


def _complete(child):
    def wait(timeout):
        child.returncode = -9
        return -9
    child.process.wait = wait
    child.cancelled.set()
    child._watch()


def test_closed_owner_is_retired_by_confirmed_monitor_after_failed_wait(monkeypatch, tmp_path):
    first, sibling = _owner(tmp_path, 'A'), _owner(tmp_path, 'B')
    monkeypatch.setattr(sessions, '_sessions', {(str(tmp_path), 'A'): first, (str(tmp_path), 'B'): sibling})
    child, other = inert_child(reaped=False), inert_child(reaped=False)
    jobs = {'A-job': (first, child), 'B-job': (sibling, other)}
    monkeypatch.setattr(native, '_jobs', jobs)
    child._on_cleanup_complete = lambda completed: native._retire_closed('A-job', first, completed)
    first.close()
    assert not first.is_live() and sibling.is_live()
    assert jobs['A-job'] == (first, child)
    waits = []

    def wait(timeout):
        waits.append(timeout)
        assert jobs['A-job'] == (first, child) and not child.finished.is_set()
        if len(waits) == 1:
            raise subprocess.TimeoutExpired('inert native process', timeout)
        child.returncode = -9
        return -9

    child.process.wait = wait
    child._watch()
    assert waits == [5, 5] and child.finished.is_set()
    assert 'A-job' not in jobs  # no second close_native or caller poll
    assert jobs['B-job'] == (sibling, other) and sibling.is_live()


def test_live_owner_completed_output_remains_until_ordinary_poll(monkeypatch, tmp_path):
    owner = _owner(tmp_path, 'live')
    monkeypatch.setattr(sessions, '_sessions', {(str(tmp_path), 'live'): owner})
    child = inert_child(reaped=False)
    child.output.put(('stdout', b'synthetic completed result'))
    jobs = {'live-job': (owner, child)}
    monkeypatch.setattr(native, '_jobs', jobs)
    child._on_cleanup_complete = lambda completed: native._retire_closed('live-job', owner, completed)
    _complete(child)
    assert jobs['live-job'] == (owner, child)
    response = native.dispatch_native(owner, {'op': 'native.poll', 'job': 'live-job'})
    assert response['ok'] and response['done'] and response['returncode'] == -9
    assert base64.b64decode(response['chunks'][0]['data']) == b'synthetic completed result'
    assert 'live-job' not in jobs


@pytest.mark.parametrize('replacement', ['child', 'owner'])
def test_completion_never_retires_a_replaced_job_tuple(monkeypatch, replacement):
    ended = SimpleNamespace(is_live=lambda: False)
    child, other = inert_child(), inert_child()
    child.finished.set()
    actual = (ended, other) if replacement == 'child' else (object(), child)
    jobs = {'reused-job': actual}
    monkeypatch.setattr(native, '_jobs', jobs)
    native._retire_closed('reused-job', ended, child)
    assert jobs['reused-job'] == actual


def _start(monkeypatch, tmp_path, constructor):
    from hermes_cli import _launchers
    from hermes_security import handoff
    from tools.environments import local

    owner = SimpleNamespace(home=tmp_path, cwd=tmp_path, session_id='fast', live=True)
    owner.is_live = lambda: owner.live
    owner.environment = lambda: {sessions.ENV_NAME: 'synthetic-operation-grant'}
    monkeypatch.setattr(local, 'served_profile_child_env', lambda **kwargs: {})
    monkeypatch.setattr(_launchers, 'runtime_command', lambda *args: ['inert-trusted-command'])
    reader, writer = os.pipe()
    os.close(writer)
    monkeypatch.setattr(handoff, 'prepare_child_key', lambda *args, **kwargs: (reader, {}))
    monkeypatch.setattr(NativeChild, '__init__', lambda self, *args, **kwargs: constructor(self, owner, kwargs))
    result = native.dispatch_native(owner, {'op': 'native.start', 'arguments': ['status']})
    with pytest.raises(OSError) as exc:
        os.fstat(reader)
    assert exc.value.errno == errno.EBADF
    return owner, result


@pytest.mark.parametrize('ended', [False, True], ids=['live-results-retained', 'closed-fast-finish-retired'])
def test_fast_completion_before_registration_preserves_owner_semantics(monkeypatch, tmp_path, ended):
    jobs = {}
    monkeypatch.setattr(native, '_jobs', jobs)
    notifications = []

    def constructor(child, owner, kwargs):
        child.__dict__.update(inert_child().__dict__)
        child.finished.set()
        child.output.put(('stdout', b'fast result'))
        owner.live = not ended
        assert not jobs
        assert callable(kwargs['_on_cleanup_complete'])
        kwargs['_on_cleanup_complete'](child)  # before NativeChild returns to registration
        notifications.append(True)

    owner, response = _start(monkeypatch, tmp_path, constructor)
    assert response['ok'] and notifications == [True]
    assert (response['job'] in jobs) is (not ended)
    if not ended:
        result = native.dispatch_native(owner, {'op': 'native.poll', 'job': response['job']})
        assert result['done'] and base64.b64decode(result['chunks'][0]['data']) == b'fast result'


def test_constructor_failure_does_not_publish_reserved_job(monkeypatch, tmp_path):
    jobs = {'unrelated-job': (object(), object())}
    original = jobs.copy()
    monkeypatch.setattr(native, '_jobs', jobs)

    def refused(child, owner, kwargs):
        raise OSError('synthetic native constructor refusal')

    _, response = _start(monkeypatch, tmp_path, refused)
    assert response['ok'] is False and jobs == original
