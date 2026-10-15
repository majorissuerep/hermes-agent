"""Deck membership inside the gateway's authorized mutation receipt transaction."""
import time


def valid_deck_payload(payload):
    fields = {
        'open': {'action', 'surface', 'cwd'},
        'close': {'action', 'reason', 'closed_by'},
    }
    action = payload.get('action')
    return (isinstance(action, str) and action in fields and set(payload) == fields[action]
            and all(isinstance(value, str) for value in payload.values())
            and bool(payload['surface' if action == 'open' else 'reason'].strip()))


def deck_in_transaction(db, conn, session_id, payload):
    root = db._deck_lineage_root_on(conn, session_id)
    if payload['action'] == 'open':
        handle = db._deck_open_on(conn, root, surface=payload['surface'], cwd=payload['cwd'])
        return {session_id}, {'handle': handle}
    row = conn.execute('SELECT handle FROM deck_sessions WHERE session_id=?', (root,)).fetchone()
    if row is None:
        return {session_id}, {'closed': False}
    from hermes_state.local_lineage import local_physical_target
    from hermes_state.mutation_guards import require_idle
    target = local_physical_target(conn, session_id)
    require_idle(db, conn, list({session_id, target}))
    reason = payload['reason']
    closed = db._deck_close_on(conn, row[0], reason=reason, closed_by=payload['closed_by'])
    if closed:
        db._end_and_bump(conn,
            'UPDATE sessions SET ended_at=?, end_reason=? WHERE id=? AND ended_at IS NULL',
            (time.time(), reason, target), target, reason)
    return {session_id, target}, {'handle': row[0], 'closed': closed}
