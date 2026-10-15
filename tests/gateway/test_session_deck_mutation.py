"""Native frontends change deck membership through the profile's fenced writer."""
from types import SimpleNamespace

import pytest

from gateway.session_authority import LiveSession, SessionAuthority
from gateway.session_controls import AuthorityConnection
from hermes_state import SessionDB
from hermes_state.runtime import begin_runtime_epoch


@pytest.mark.asyncio
async def test_deck_membership_is_scoped_authorized_and_exact_retry_safe(tmp_path):
    stores, owners, viewers = {}, {}, {}
    try:
        for profile in ('a', 'b'):
            home = tmp_path / profile
            home.mkdir()
            db = stores[profile] = SessionDB(db_path=home / 'state.db')
            db.create_session('same-id', source='cli')
            epoch = begin_runtime_epoch(db, instance_id=profile)
            authority = SessionAuthority(SimpleNamespace(_draining=False), profile_id=profile,
                                         instance_id=profile, db=db, epoch=epoch)
            authority.sessions['same-id'] = LiveSession(None, 'route')
            owners[profile] = AuthorityConnection(authority, object(), {'user_id': 'human'})
            viewers[profile] = AuthorityConnection(authority, object(), {
                'user_id': 'reader', 'capabilities': ['session:read']})
        requests = {}
        for profile in ('a', 'b', 'a'):
            request = requests.setdefault(profile, {'id': profile, 'method': 'session.mutate', 'params': {
                'session_id': 'same-id', 'request_id': 'deck-open', 'expected_revision': 0,
                'operation': 'deck', 'payload': {'action': 'open', 'surface': 'cli', 'cwd': '/' + profile}}})
            denied = await viewers[profile].dispatch(request)
            assert denied['error']['message'] == 'permission_denied'
            first = await owners[profile].dispatch(request)
            assert 'result' in first, first
            assert await owners[profile].dispatch(request) == first
            row = stores[profile].deck_row(first['result']['handle'])
            assert row['cwd'] == '/' + profile and row['closed_at'] is None
            assert stores[profile].get_session('same-id')['runtime_revision'] == 1
        close = {'id': 'close', 'method': 'session.mutate', 'params': {
            'session_id': 'same-id', 'request_id': 'deck-close', 'expected_revision': 1,
            'operation': 'deck', 'payload': {'action': 'close', 'reason': 'deck_close', 'closed_by': '/close'}}}
        closed = await owners['a'].dispatch(close)
        assert closed['result']['closed'] is True, closed
        assert await owners['a'].dispatch(close) == closed
        assert stores['a'].deck_row_for_session('same-id')['closed_by'] == '/close'
        assert stores['a'].get_session('same-id')['end_reason'] == 'deck_close'
        assert stores['b'].deck_row_for_session('same-id')['closed_at'] is None
        from hermes_state.runtime import admit_session_input
        admit_session_input(stores['b'], epoch=owners['b'].authority.epoch, principal_id='human',
                            session_id='same-id', request_id='input', payload={'text': 'still queued'})
        refused = await owners['b'].dispatch(close)
        assert refused['error']['message'] == 'session_busy', refused
        assert stores['b'].deck_row_for_session('same-id')['closed_at'] is None
    finally:
        for connection in [*owners.values(), *viewers.values()]:
            await connection.close()
        for db in stores.values():
            db.close()
