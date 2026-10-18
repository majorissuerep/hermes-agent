"""Historical fixture data, seeded without restoring executable retired runtime APIs."""
from contextlib import contextmanager
import json


def seed_admission(db, session_id, *, status='queued', admission_id='legacy-input', payload=None):
    db._write_sql('INSERT INTO session_admissions(admission_id,request_id,principal_id,target_session_id,lineage_json,payload_json,payload_digest,intent,status) VALUES(?,?,?,?,?,?,?,?,?)',
                 (admission_id,admission_id,'old-owner',session_id,'[]',json.dumps(payload or {}),'old-digest','queue',status))
    return admission_id


def seed_topic_mode(db, *, chat_id, user_id, profile_name):
    db._write_sql('CREATE TABLE IF NOT EXISTS telegram_dm_topic_mode(profile_name TEXT NOT NULL,chat_id TEXT NOT NULL,user_id TEXT NOT NULL,activated_at REAL,updated_at REAL,PRIMARY KEY(profile_name,chat_id))')
    db._write_sql('INSERT INTO telegram_dm_topic_mode VALUES(?,?,?,?,?)',(profile_name,chat_id,user_id,0,0))


def seed_topic_binding(db, *, chat_id,thread_id,user_id,session_key,session_id,profile_name):
    db._write_sql('CREATE TABLE IF NOT EXISTS telegram_dm_topic_bindings(profile_name TEXT NOT NULL,chat_id TEXT NOT NULL,thread_id TEXT NOT NULL,user_id TEXT,session_key TEXT,session_id TEXT,PRIMARY KEY(profile_name,chat_id,thread_id))')
    db._write_sql('INSERT INTO telegram_dm_topic_bindings VALUES(?,?,?,?,?,?)',
                 (profile_name,chat_id,thread_id,user_id,session_key,session_id))


@contextmanager
def delivery_connection(db):
    from hermes_state.legacy_delivery_schema import initialize_schema
    with db._lock:
        initialize_schema(db._conn)
        yield db._conn
