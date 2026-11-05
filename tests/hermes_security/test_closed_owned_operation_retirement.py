"""Completed fixed jobs release closed owners while preserving live-owner output."""
from contextlib import nullcontext
import base64
import json
import threading
import time
from types import SimpleNamespace

import pytest

from hermes_cli.management_owned_jobs import OwnedManagementJob
from hermes_security import authority_native, authority_owned_operations


@pytest.mark.parametrize('closed', [False, True], ids=['live-owner-output', 'closed-owner-retirement'])
def test_completed_fixed_operation_retires_closed_owner_without_another_close(monkeypatch, tmp_path, closed):
    owner = SimpleNamespace(home=tmp_path, session_id='fixed-owner', live=True)
    owner.is_live = lambda: owner.live
    job = OwnedManagementJob.__new__(OwnedManagementJob)
    job.home, job.operation = tmp_path, 'browser.outgoing.repair'
    job.finished = threading.Event()
    job.last_poll = time.monotonic()
    job.budget = SimpleNamespace(scope=nullcontext, check=lambda: None, cancelled=threading.Event())
    job.returncode, job.reason, job.result = None, None, None
    job._offset, job._encoded = 0, None
    started = []
    job.worker = SimpleNamespace(start=lambda: started.append(True))
    jobs = {'foreign': (object(), object())}
    foreign = jobs['foreign']
    lock = threading.RLock()
    monkeypatch.setattr(authority_native, '_jobs', jobs)
    monkeypatch.setattr(authority_native, '_lock', lock)
    monkeypatch.setitem(authority_owned_operations._FACTORIES, job.operation, lambda home: job)
    monkeypatch.setattr('agent.secret_scope.build_profile_secret_scope', lambda home: {})
    monkeypatch.setattr('tools.terminal_scope.build_profile_terminal_scope', lambda *args, **kwargs: {})
    monkeypatch.setattr('hermes_cli.management.execute', lambda request, home: {'saved': ['owned-reference'], 'retained': []})

    response = authority_owned_operations.start(owner, job.operation, jobs, lock)
    identity = response['job']
    assert started == [True] and jobs[identity] == (owner, job)
    owner.live = not closed
    job._run()  # Real completion method, with inert worker and fixed domain result.

    assert job.finished.is_set() and job.returncode == 0
    if closed:
        assert identity not in jobs
    else:
        assert jobs[identity] == (owner, job)
        result = authority_native.dispatch_native(owner, {'op': 'native.poll', 'job': identity})
        body = b''.join(base64.b64decode(chunk['data']) for chunk in result['chunks'])
        assert result['ok'] and result['done'] and result['returncode'] == 0
        assert json.loads(body)['result'] == {'saved': ['owned-reference'], 'retained': []}
        assert identity not in jobs
    assert jobs['foreign'] == foreign
