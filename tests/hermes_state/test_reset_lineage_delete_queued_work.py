"""A local reset conversation with accepted queued work survives every legacy delete and sweep
of its current physical segment (JoaoMarcos44 R1, andrexibiza 8, dokterdok corr-reset-delete).

Canonical reset keeps the creation id S0 as the owner (policy, FIFO, generation) and moves the
transcript to child S1; a follower queued on S0 runs against S1. Deleting or sweeping S1 alone
used to retire S0's policy and leave the queued admission with nothing to run on."""
from contextlib import closing
import time

import pytest

from hermes_state import SessionDB
import json
from types import SimpleNamespace
from hermes_state.errors import RuntimeStoreError
from tests.hermes_state.legacy_rows import seed_admission
rt = SimpleNamespace(RuntimeStoreError=RuntimeStoreError)
from hermes_state.local import POLICY_PREFIX

OPERATIONS = {
    'single': lambda db, s0, s1: db.delete_session(s1, exclude_active_write_guards=True),
    'bulk': lambda db, s0, s1: db.delete_sessions([s1], exclude_active_write_guards=True),
    'never-active': lambda db, s0, s1: db.prune_never_active_keyed_sessions(older_than_days=30),
    'prune-child': lambda db, s0, s1: db.prune_sessions(older_than_days=30, started_before=time.time() - 30 * 86400),
    'prune-all': lambda db, s0, s1: db.prune_sessions(older_than_days=None, last_active_before=time.time() + 60),
    # The empty-session sweep shares retire_prunable: an ended, still-empty reset tip is not an empty chat.
    'prune-empty': lambda db, s0, s1: db.delete_empty_sessions(),
}


def _conversation(tmp_path, operation, queued):
    db = SessionDB(tmp_path / 'state.db')
    s0, s1 = 'old-logical-owner', 'old-reset-target'
    db.create_session(s0, 'cli', chat_id=s0)
    db.append_message(s0, 'user', 'root history')
    db.end_session(s0, 'session_reset')
    db.create_session(s1, 'cli', parent_session_id=s0, chat_id=s0,
                      session_key='agent:main:cli:old-reset-target', model_config={'_reset_from': s0})
    receipt = {'session_id': s0, 'entry': {'session_id': s1}, 'lineage': [s0, s1]}
    db._write_sql('INSERT INTO state_meta(key,value) VALUES(?,?)', (POLICY_PREFIX+s0,json.dumps(receipt)))
    old = time.time() - 200 * 86400
    # Aged past every cutoff; the current segment ended for the ended-only prune, open for never-active.
    db._execute_write(lambda c: c.execute('UPDATE sessions SET started_at=? WHERE id IN (?,?)', (old, s0, s1)))
    if operation.startswith('prune'):
        db._execute_write(lambda c: c.execute('UPDATE sessions SET ended_at=? WHERE id=?', (old, s1)))
    admission = {'admission_id': seed_admission(db, s0)} if queued else None
    return db, s0, s1, admission


def _state(db, s0, s1):
    with db._read_ctx() as conn:
        policy = conn.execute('SELECT 1 FROM state_meta WHERE key=?', (POLICY_PREFIX + s0,)).fetchone() is not None
    return {'s0': db.get_session(s0) is not None, 's1': db.get_session(s1) is not None, 'policy': policy}


@pytest.mark.parametrize('operation', sorted(OPERATIONS))
def test_segment_delete_or_sweep_keeps_a_conversation_with_queued_work(tmp_path, operation):
    db, s0, s1, admission = _conversation(tmp_path, operation, queued=True)
    with closing(db):
        if operation == 'never-active':
            assert s1 in [r['id'] for r in db.list_never_active_keyed_sessions(older_than_days=30)]
        try:
            OPERATIONS[operation](db, s0, s1)
        except rt.RuntimeStoreError as exc:
            assert operation in {'single', 'bulk'} and exc.reason == 'session_busy', (operation, exc.reason)
        assert _state(db, s0, s1) == {'s0': True, 's1': True, 'policy': True}, operation
        assert db._read_one('SELECT status FROM session_admissions WHERE admission_id=?', (admission['admission_id'],))['status'] == 'queued'


@pytest.mark.parametrize('operation', ['single', 'bulk', 'prune-all'])
def test_idle_conversation_deletes_whole_from_its_current_segment(tmp_path, operation):
    db, s0, s1, _ = _conversation(tmp_path, operation, queued=False)
    with closing(db):
        if operation == 'never-active':
            assert s1 in [r['id'] for r in db.list_never_active_keyed_sessions(older_than_days=30)]
        OPERATIONS[operation](db, s0, s1)
        assert _state(db, s0, s1) == {'s0': False, 's1': False, 'policy': False}, operation
