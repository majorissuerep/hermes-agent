"""Validate passive lineage binding records from historical local sessions."""
import json
from hermes_state.errors import RuntimeStoreError

LEGACY_PREFIX = 'gateway.local_legacy.v1:'

def legacy_lineage_root(conn, receipt):
    root = receipt.get('legacy_session_id', receipt['session_id'])
    if 'legacy_session_id' in receipt:
        if root != receipt['session_id']:
            raise RuntimeStoreError('storage_unavailable')
        row = conn.execute('SELECT value FROM state_meta WHERE key=?', (LEGACY_PREFIX + root,)).fetchone()
        expected = {k: receipt[k] for k in ('session_id', 'profile_id', 'principal_id', 'legacy_session_id')}
        if row is None or json.loads(row[0]) != expected:
            raise RuntimeStoreError('storage_unavailable')
    return root
