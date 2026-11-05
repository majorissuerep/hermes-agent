"""Ancestor revocation retires completed descendants without cancelling other jobs."""
import base64

import pytest

from hermes_security import authority_native as native
from hermes_security import session_authority as sessions
from tests.hermes_platform.test_native_cleanup_confirmation import inert_child
from tests.hermes_security.test_native_closed_owner_retirement import _owner, _complete


def test_ancestor_close_sweeps_completed_derived_work_but_preserves_pending_and_live(monkeypatch, tmp_path):
    parent, derived, sibling = [_owner(tmp_path, name) for name in ('A', 'derived-A', 'B')]
    derived.parent = parent
    monkeypatch.setattr(sessions, '_sessions', {(str(tmp_path), o.session_id): o for o in (parent, derived, sibling)})
    done, pending, live = [inert_child(reaped=False) for _ in range(3)]
    jobs = {'derived-done': (derived, done), 'derived-pending': (derived, pending), 'live-done': (sibling, live)}
    monkeypatch.setattr(native, '_jobs', jobs)
    done._on_cleanup_complete = lambda child: native._retire_closed('derived-done', derived, child)
    live.output.put(('stdout', b'live sibling result'))
    _complete(done)
    _complete(live)
    assert len(jobs) == 3 and derived.is_live()
    parent.close()  # No DIRECT jobs: retirement must still run on this edge.
    assert not derived.is_live() and sibling.is_live()
    assert 'derived-done' not in jobs
    assert jobs['derived-pending'] == (derived, pending) and not pending.cancelled.is_set()
    assert jobs['live-done'] == (sibling, live)
    response = native.dispatch_native(sibling, {'op': 'native.poll', 'job': 'live-done'})
    assert response['ok'] and response['done']
    assert base64.b64decode(response['chunks'][0]['data']) == b'live sibling result'


@pytest.mark.parametrize('references', [1, 2], ids=['last-reference', 'live-reference-first'])
def test_revocation_sweep_keeps_live_alias_results_and_only_cancels_direct_owner(monkeypatch, tmp_path, references):
    parent, derived, sibling = [_owner(tmp_path, name) for name in ('A', 'derived-A', 'B')]
    derived.parent = parent
    parent.references = references
    owners = {(str(tmp_path), o.session_id): o for o in (parent, derived, sibling)}
    owners[(str(tmp_path), 'alias-A')] = parent
    unrelated = _owner(tmp_path, 'unrelated-C')
    owners[(str(tmp_path), unrelated.session_id)] = unrelated
    monkeypatch.setattr(sessions, '_sessions', owners)
    original, finished, pending, other = [inert_child(reaped=False) for _ in range(4)]
    jobs = {'direct': (parent, original), 'derived-done': (derived, finished),
            'derived-pending': (derived, pending), 'other-pending': (sibling, other)}
    monkeypatch.setattr(native, '_jobs', jobs)
    finished.output.put(('stdout', b'completed alias output'))
    _complete(finished)
    if references == 2:
        parent.close()
        assert parent.is_live() and derived.is_live()
        assert 'derived-done' in jobs and not original.cancelled.is_set()
        # A different close edge must not discard this still-live alias result.
        unrelated.close()
        assert 'derived-done' in jobs
    parent.close()
    assert not parent.is_live() and not derived.is_live() and sibling.is_live()
    assert original.cancelled.is_set() and jobs['direct'] == (parent, original)
    assert 'derived-done' not in jobs
    assert not pending.cancelled.is_set() and not other.cancelled.is_set()
    assert jobs['derived-pending'] == (derived, pending) and jobs['other-pending'] == (sibling, other)
