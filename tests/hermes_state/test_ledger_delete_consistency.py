"""Native deletion preserves uncertain historical work and retires settled receipts atomically."""
from contextlib import closing
import json
import time
import pytest
from hermes_state import SessionDB
from hermes_state.errors import RuntimeStoreError
from hermes_state.legacy_receipts import ADMISSION_PREFIX
from hermes_state.mutation_retirement import RETIRED_PREFIX
from tests.hermes_state.legacy_rows import seed_admission


@pytest.mark.parametrize('delete', [
    lambda db, sid: db.delete_session(sid),
    lambda db, sid: db.delete_sessions([sid]),
    lambda db, sid: db.delete_session_if_empty(sid),
], ids=['single', 'bulk', 'empty'])
def test_native_delete_retires_settled_input_and_preserves_queued_work(tmp_path, delete):
    with closing(SessionDB(tmp_path / 'state.db')) as db:
        for sid in ('settled', 'busy'):
            db.create_session(sid, 'cli')
        seed_admission(db, 'settled', status='terminal', admission_id='settled-input')
        seed_admission(db, 'busy', admission_id='queued-input')
        assert delete(db, 'settled')
        assert db.get_session('settled') is None
        saved = json.loads(db.get_meta(ADMISSION_PREFIX + 'settled-input'))
        assert saved['status'] == 'terminal' and saved['payload_json'] == '{}'
        assert db.get_meta(RETIRED_PREFIX + 'settled') is not None
        with pytest.raises(RuntimeStoreError, match='not_found'):
            db.ensure_session('settled', 'cli')
        with pytest.raises(RuntimeStoreError, match='session_busy'):
            delete(db, 'busy')
        assert db.get_session('busy') is not None
        assert db._read_one('SELECT status FROM session_admissions WHERE admission_id=?', ('queued-input',))['status'] == 'queued'
        assert not db._read_all('PRAGMA foreign_key_check')


def test_passive_prune_skips_unknown_work_and_does_not_erase_its_payload(tmp_path):
    with closing(SessionDB(tmp_path / 'state.db')) as db:
        old = time.time() - 100 * 86400
        for sid in ('done', 'uncertain'):
            db.create_session(sid, 'cli')
            db._write_sql('UPDATE sessions SET started_at=?,ended_at=? WHERE id=?',(old,old,sid))
        seed_admission(db, 'done', status='terminal', admission_id='done-input')
        seed_admission(db, 'uncertain', status='unknown', admission_id='uncertain-input', payload={'text':'preserve accepted user turn'})
        db.prune_sessions(older_than_days=30)
        assert db.get_session('done') is None
        assert db.get_session('uncertain') is not None
        saved = db._read_one('SELECT payload_json FROM session_admissions WHERE admission_id=?', ('uncertain-input',))
        assert json.loads(saved['payload_json'])['text'] == 'preserve accepted user turn'
