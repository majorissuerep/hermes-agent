"""Legacy schedules stay passive until a user explicitly claims a local destination."""
import secrets
import threading
import time

import pytest


@pytest.fixture
def schedule_home(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_security import vault
    from hermes_cli import goals

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    token = set_hermes_home_override(tmp_path)
    password = secrets.token_urlsafe(24)
    vault.init_vault(tmp_path, password)
    vault.unlock(tmp_path, password)
    goals._DB_CACHE.clear()
    db = goals._get_session_db()
    assert db is not None
    try:
        yield db
    finally:
        goals._DB_CACHE.clear()
        db.close()
        vault.lock_now(tmp_path)
        reset_hermes_home_override(token)


def _legacy_manager(kind, sid, route):
    from hermes_cli.heartbeat import HeartbeatManager, HeartbeatState, save_heartbeat
    from hermes_cli.loops import LoopManager, LoopState, save_loop

    past = time.time() - 3600
    if kind == 'heartbeat':
        save_heartbeat(sid, HeartbeatState(prompt='retained instruction', interval_seconds=60, created_at=past))
        return HeartbeatManager(sid), 'heartbeat:' + sid
    save_loop(sid, LoopState(prompt='retained instruction', interval_seconds=60,
                            created_at=past, next_due_at=past, route=route))
    return LoopManager(sid), 'loop:' + sid


def _fire(kind, manager):
    return manager.due_prompt() if kind == 'heartbeat' else manager.fire_tick()


def _make_due(kind, manager):
    from hermes_cli.heartbeat import save_heartbeat
    from hermes_cli.loops import save_loop

    past = time.time() - 3600
    manager.state.created_at = manager.state.last_fired_at = past
    if kind == 'heartbeat':
        save_heartbeat(manager.session_id, manager.state)
    else:
        manager.state.next_due_at = past
        save_loop(manager.session_id, manager.state)


@pytest.mark.parametrize('kind,route', [
    ('heartbeat', {}), ('loop', {'platform': 'telegram', 'chat_id': 'old-chat'}),
    ('loop', {'platform': 'telegram'}), ('loop', {'chat_id': 'old-chat'}),
])
def test_remote_schedule_is_passive_until_explicit_local_resume(schedule_home, monkeypatch, kind, route):
    from tui_runtime import server

    db = schedule_home
    sid = 'legacy-' + kind
    db.create_session(sid, source='telegram')
    db.append_message(sid, 'user', 'retained conversation prefix')
    manager, key = _legacy_manager(kind, sid, route)
    before, history = db.get_meta(key), db.get_messages(sid)
    assert _fire(kind, manager) is None
    assert db.get_meta(key) == before
    session = {'session_key': sid, 'history_lock': threading.Lock(), 'running': False}
    submitted = []
    monkeypatch.setattr(server, '_run_prompt_submit', lambda *args, **kwargs: submitted.append(args) or True)
    monkeypatch.setattr(server, '_emit', lambda *args: None)
    tick = getattr(server, '_maybe_fire_tui_' + kind + '_tick')
    tick('ui-' + sid, session)
    assert submitted == [] and not session['running']
    assert db.get_meta(key) == before
    assert manager.resume() is not None
    _make_due(kind, manager)
    tick('ui-' + sid, session)
    assert len(submitted) == 1 and 'retained instruction' in submitted[0][3]
    assert db.get_messages(sid) == history
    assert db.get_session(sid)['source'] == 'telegram'


@pytest.mark.parametrize('kind', ['heartbeat', 'loop'])
@pytest.mark.parametrize('source', ['cli', 'tui'])
def test_existing_unrouted_native_schedule_still_fires(schedule_home, kind, source):
    sid = source + '-' + kind
    schedule_home.create_session(sid, source=source)
    manager, _ = _legacy_manager(kind, sid, {})
    assert 'retained instruction' in _fire(kind, manager)
