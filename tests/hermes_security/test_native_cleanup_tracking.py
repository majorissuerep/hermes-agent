"""Pending native cleanup remains owned through cancel, poll and owner revocation."""
import time
from types import SimpleNamespace

import pytest

from hermes_security import authority_native
from tests.hermes_platform.test_native_cleanup_confirmation import inert_child


@pytest.mark.parametrize('complete', [False, True], ids=['pending', 'reaped'])
def test_cancel_and_owner_close_retain_handle_until_cleanup_confirmation(monkeypatch, complete):
    child = inert_child(reaped=complete)
    child.finished.set()
    owner, sibling = object(), object()
    sibling_child = object()
    jobs = {'owned': (owner, child), 'sibling': (sibling, sibling_child)}
    monkeypatch.setattr(authority_native, '_jobs', jobs)
    present_during_join = []
    child.monitor.join = lambda timeout: present_during_join.append(jobs.get('owned') == (owner, child))
    response = authority_native.dispatch_native(owner, {'op': 'native.cancel', 'job': 'owned'})
    assert response['ok'] is complete
    assert ('owned' in jobs) is (not complete)
    if not complete:
        response = authority_native.dispatch_native(owner, {'op': 'native.poll', 'job': 'owned'})
        assert response['ok'] and not response['done'] and response['returncode'] is None
        authority_native.close_native(owner)
        assert jobs['owned'] == (owner, child)
        child.returncode = -9
        authority_native.close_native(owner)
        assert 'owned' not in jobs
    assert present_during_join and all(present_during_join)
    assert jobs['sibling'] == (sibling, sibling_child)


def test_stale_pending_native_handles_still_count_towards_owner_limit(monkeypatch, tmp_path):
    owner = SimpleNamespace(home=tmp_path, session_id="synthetic-owner", is_live=lambda: True)
    children = [inert_child(reaped=False) for _ in range(4)]
    for child in children:
        child.finished.set()
        child.last_poll = time.monotonic() - 30
    jobs = {str(index): (owner, child) for index, child in enumerate(children)}
    monkeypatch.setattr(authority_native, '_jobs', jobs)

    def refuse_preparation(**kwargs):
        raise AssertionError('Pending slots must refuse before child environment preparation')

    monkeypatch.setattr('tools.environments.local.served_profile_child_env', refuse_preparation)
    response = authority_native.dispatch_native(owner, {'op': 'native.start', 'arguments': ['status']})
    assert response == {'ok': False, 'native_error': 'the owning session already has four native commands'}
    assert len(jobs) == 4
