"""Local physical-target handoff inside the existing publication transaction.

The creation ID remains the admission/execution owner. Transcript rotation must
not re-key idempotency digests, queued inputs, subscribers or uncertain claims.
"""
import json
import time

from hermes_state.local import POLICY_PREFIX
from hermes_state.errors import RuntimeStoreError
from hermes_state.ledger_values import _json
import itertools


def validate_local_lineage(conn, receipt):
    from hermes_state.compression import _CHAIN_STEP_SQL
    from hermes_state.keys import local_identity
    if ('legacy_session_id' not in receipt and
            receipt['session_id'] != local_identity(receipt['profile_id'], receipt['principal_id'], receipt['request_id'])):
        raise RuntimeStoreError('storage_unavailable')
    from hermes_state.local_migration import legacy_lineage_root
    root = legacy_lineage_root(conn, receipt)
    lineage = receipt.get('lineage', [root])
    if (not isinstance(lineage, list) or not lineage or len(set(lineage)) != len(lineage)
            or lineage[0] != root or lineage[-1] != receipt['entry']['session_id']):
        raise RuntimeStoreError('storage_unavailable')
    for parent_id, child_id in itertools.pairwise(lineage):
        parent = conn.execute('SELECT end_reason FROM sessions WHERE id=?', (parent_id,)).fetchone()
        child = conn.execute('SELECT parent_session_id,model_config FROM sessions WHERE id=?', (child_id,)).fetchone()
        if parent is None or child is None or child['parent_session_id'] != parent_id:
            raise RuntimeStoreError('storage_unavailable')
        if parent['end_reason'] == 'session_reset':
            valid = json.loads(child['model_config'] or '{}').get('_reset_from') == parent_id
        else:
            tip = conn.execute(_CHAIN_STEP_SQL, (parent_id,)).fetchone()
            valid = parent['end_reason'] == 'compression' and tip is not None and tip['id'] == child_id
        if not valid:
            raise RuntimeStoreError('storage_unavailable')
    if conn.execute(_CHAIN_STEP_SQL, (lineage[-1],)).fetchone() is not None:
        raise RuntimeStoreError('storage_unavailable')
    return lineage[-1]


def local_physical_target(conn, session_id):
    """Resolve a local owner's current transcript on the caller's transaction."""
    saved = conn.execute('SELECT value FROM state_meta WHERE key=?',
                         (POLICY_PREFIX + session_id,)).fetchone()
    if saved is None:
        return session_id
    receipt = json.loads(saved[0])
    if receipt['session_id'] != session_id:
        raise RuntimeStoreError('storage_unavailable')
    return validate_local_lineage(conn, receipt)


def advance_local_target(conn, parent_session_id, child_session_id, *, entry=None):
    parent = conn.execute('SELECT * FROM sessions WHERE id=?', (parent_session_id,)).fetchone()
    logical_id = parent['chat_id'] if parent else None
    if not logical_id:
        return
    key = POLICY_PREFIX + logical_id
    raw = conn.execute('SELECT value FROM state_meta WHERE key=?', (key,)).fetchone()
    if raw is None:
        from hermes_state.local_migration import LEGACY_PREFIX
        legacy = conn.execute('SELECT 1 FROM state_meta WHERE key=?', (LEGACY_PREFIX + logical_id,)).fetchone()
        if logical_id.startswith('local-') or legacy:
            raise RuntimeStoreError('storage_unavailable')
        return
    try:
        receipt = json.loads(raw[0])
        if (receipt['session_id'] != logical_id or receipt['entry']['session_id'] != parent_session_id
                or receipt['principal_id'] != parent['user_id'] or receipt['route'] != parent['session_key']):
            raise RuntimeStoreError('storage_unavailable')
        route_row = conn.execute("SELECT entry_json FROM gateway_routing WHERE scope='' AND session_key=?",
                                 (receipt['route'],)).fetchone()
        routed = json.loads(route_row[0]) if route_row else None
        if routed is None or routed['session_id'] != parent_session_id:
            raise RuntimeStoreError('admission_conflict')
        candidate = dict(entry or routed, session_id=child_session_id)
        receipt['entry'] = candidate
        receipt['lineage'] = [*receipt.get('lineage', [logical_id]), child_session_id]
        validate_local_lineage(conn, receipt)
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, RuntimeStoreError):
            raise
        raise RuntimeStoreError('storage_unavailable') from exc
    conn.execute("UPDATE gateway_routing SET entry_json=? WHERE scope='' AND session_key=?",
                 (_json(candidate), receipt['route']))
    conn.execute('UPDATE state_meta SET value=? WHERE key=?', (_json(receipt), key))
    conn.execute('UPDATE sessions SET runtime_revision=runtime_revision+1 WHERE id=?', (logical_id,))
