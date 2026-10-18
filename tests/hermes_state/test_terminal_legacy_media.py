"""Historical media cleanup retains live/history owners and never escapes its managed root."""
import hashlib
import json
from pathlib import Path
import pytest
from hermes_state import SessionDB
from hermes_state import media


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'home'))
    db = SessionDB(tmp_path / 'state.db')
    db.create_session('history', 'cli')
    yield db
    db.close()


def reference(tmp_path):
    digest = hashlib.sha256(b'owned document').hexdigest()
    root = media._media_root()
    path = root / digest / 'document.txt'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('owned document')
    return {'path': str(path), 'sha256': digest}


def test_history_and_late_transcript_writer_keep_retired_document(store, tmp_path, monkeypatch):
    ref = reference(tmp_path)
    store.append_message('history', 'user', f"Keep {ref['path']}")
    assert media.release_unheld_media(store, [ref]) == 0
    assert Path(ref['path']).exists()
    # The initial read snapshot saw no second file; a transcript writer lands before collection.
    second = dict(ref, path=str(Path(ref['path']).with_name('second.txt')))
    Path(second['path']).write_text('second owned document')
    write = store._execute_write
    def after_snapshot(fn, *args, **kwargs):
        monkeypatch.setattr(store, '_execute_write', write)
        store.append_message('history', 'user', second['path'])
        return write(fn, *args, **kwargs)
    monkeypatch.setattr(store, '_execute_write', after_snapshot)
    assert media.release_unheld_media(store, [second]) == 0
    assert Path(second['path']).exists()


def test_queued_legacy_owner_blocks_delete_but_unheld_document_is_collected(store, tmp_path):
    ref = reference(tmp_path)
    payload = json.dumps({'native_text_v1': {'media': [ref]}})
    store._write_sql('INSERT INTO session_admissions(admission_id,request_id,principal_id,target_session_id,lineage_json,payload_json,payload_digest,intent,status) VALUES(?,?,?,?,?,?,?,?,?)',
                     ('old-queued', 'old-request', 'old-owner', 'history', '[]', payload, 'digest', 'queue', 'queued'))
    assert media.release_unheld_media(store, [ref]) == 0
    assert Path(ref['path']).exists()
    store._write_sql("UPDATE session_admissions SET status='terminal',payload_json='{}' WHERE admission_id='old-queued'")
    assert media.release_unheld_media(store, [ref]) == 1
    assert not Path(ref['path']).exists()


def test_external_or_symlink_candidate_never_gains_deletion_authority(store, tmp_path):
    ref = reference(tmp_path)
    external = tmp_path / 'outside.txt'
    external.write_text('must survive')
    assert media.release_unheld_media(store, [dict(ref, path=str(external))]) == 0
    candidate = Path(ref['path'])
    candidate.unlink()
    candidate.symlink_to(external)
    assert media.release_unheld_media(store, [ref]) == 0
    assert candidate.is_symlink() and external.read_text() == 'must survive'
