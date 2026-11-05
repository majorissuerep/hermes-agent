"""Fixed maintenance must not free a slot held by an unreaped native child."""
import threading
import time
from types import SimpleNamespace

import pytest

from hermes_platform.authority_children import NativeChild
from hermes_security import authority_owned_operations


@pytest.mark.parametrize('reaped', [False, True], ids=['pending-native', 'reaped-native'])
def test_fixed_operation_counts_pending_native_slots_and_reuses_completed_slots(
        monkeypatch, tmp_path, reaped):
    owner = SimpleNamespace(home=tmp_path, session_id='fixed-operation-owner', is_live=lambda: True)
    sibling = object()
    native = NativeChild.__new__(NativeChild)
    native.finished = threading.Event()
    native.finished.set()  # A stale monitor flag is not process completion.
    native.last_poll = time.monotonic() - 30
    native.returncode = 0 if reaped else None
    native.process = SimpleNamespace(poll=lambda: native.returncode)
    native.threads, native.descendants = [], {}
    pending = SimpleNamespace(finished=threading.Event(), last_poll=time.monotonic())
    jobs = {'native': (owner, native), 'foreign': (sibling, pending)}
    jobs.update({str(index): (owner, pending) for index in range(3)})
    started = []
    fixed = SimpleNamespace(start=lambda: started.append(True))
    monkeypatch.setitem(authority_owned_operations._FACTORIES, 'browser.outgoing.repair', lambda home: fixed)
    monkeypatch.setattr('agent.secret_scope.build_profile_secret_scope', lambda home: {})
    monkeypatch.setattr('tools.terminal_scope.build_profile_terminal_scope', lambda *args, **kwargs: {})

    if reaped:
        response = authority_owned_operations.start(owner, 'browser.outgoing.repair', jobs, threading.RLock())
        assert response['ok'] and jobs[response['job']] == (owner, fixed)
        assert 'native' not in jobs and started == [True]
    else:
        with pytest.raises(ValueError, match='four native commands'):
            authority_owned_operations.start(owner, 'browser.outgoing.repair', jobs, threading.RLock())
        assert jobs['native'] == (owner, native) and not started
    assert jobs['foreign'] == (sibling, pending)
    assert sum(authority is owner for authority, _ in jobs.values()) == 4


def test_owner_close_retains_pending_native_and_fixed_jobs_while_settling_completed_job(monkeypatch, caplog):
    from hermes_cli.management_owned_jobs import OwnedManagementJob
    from hermes_security import authority_native
    from tests.hermes_platform.test_native_cleanup_confirmation import inert_child

    owner, sibling = object(), object()
    native = inert_child(reaped=False)
    fixed, completed = (OwnedManagementJob.__new__(OwnedManagementJob) for _ in range(2))
    joined = []
    for label, child in [('pending', fixed), ('complete', completed)]:
        child.finished = threading.Event()
        child.budget = SimpleNamespace(cancelled=threading.Event())
        child.worker = SimpleNamespace(join=lambda timeout, name=label: joined.append(name))
    completed.finished.set()
    unrelated = SimpleNamespace(finished=threading.Event())
    jobs = {'native': (owner, native), 'fixed': (owner, fixed), 'complete': (owner, completed),
            'foreign': (sibling, unrelated)}
    monkeypatch.setattr(authority_native, '_jobs', jobs)

    authority_native.close_native(owner)

    assert set(jobs) == {'native', 'fixed', 'foreign'}
    assert jobs['native'] == (owner, native) and jobs['fixed'] == (owner, fixed)
    assert native.cancelled.is_set() and fixed.budget.cancelled.is_set() and completed.budget.cancelled.is_set()
    assert joined == ['pending', 'complete']
    assert 'domain recovery references retained' in caplog.text
    assert 'Native command cleanup remains pending' in caplog.text
