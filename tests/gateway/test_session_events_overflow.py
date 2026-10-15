"""A subscriber that overflows its bounded mailbox must learn it, not stay silently stale."""
import asyncio
import queue
import threading
from types import SimpleNamespace

import pytest


class Peer:
    def __init__(self, gate=None):
        self.frames = queue.Queue()
        self.gate = gate
        self.write_started = threading.Event()

    def write(self, frame):
        self.write_started.set()
        if self.gate is not None:
            self.gate.wait()
        self.frames.put(frame)
        return True

    def close(self):
        return None


@pytest.mark.asyncio
async def test_overflowed_subscriber_is_detached_and_told_to_resume(tmp_path):
    from gateway.session_authority import LiveSession, SessionAuthority
    from gateway.session_contract import SessionRef
    from gateway.session_controls import AuthorityConnection
    from hermes_state import SessionDB
    from hermes_state.runtime import begin_runtime_epoch
    from tui_gateway.transport import FanoutTransport

    with SessionDB(db_path=tmp_path / 'state.db') as db:
        db.create_session('s', source='test')
        epoch = begin_runtime_epoch(db, instance_id='owner')
        authority = SessionAuthority(SimpleNamespace(_draining=False), profile_id='owned', instance_id='owner', db=db, epoch=epoch)
        live = authority.sessions['s'] = LiveSession(None, 'route')
        ref = SessionRef('owned', 's')
        gate = threading.Event()
        healthy, slow = Peer(), Peer(gate)
        fast, stalled = [AuthorityConnection(authority, peer, {'user_id': 'viewer-' + str(i)}) for i, peer in enumerate((healthy, slow))]
        try:
            await fast.resume(ref, {})
            await stalled.resume(ref, {})
            assert len(live.subscribers) == 2
            total = FanoutTransport._MAX_PENDING_FRAMES + 2
            delivered = []
            for n in range(total):
                live.event_stream.publish('s', {'n': n}, event_type='message.delta')
                # Pace the healthy reader by receipt so only the stalled peer's backlog grows.
                delivered.append(await asyncio.to_thread(healthy.frames.get, True, 5))
                if n == 0:
                    # Pin one slow frame in flight before filling its bounded queue. Otherwise
                    # a late-starting writer can take a whole batch and never reach overflow.
                    assert await asyncio.to_thread(slow.write_started.wait, 5), 'slow writer never started'
            # The healthy viewer saw the whole turn; the stalled one lost its subscription
            # while its socket stayed open, and its next frame says so.
            assert [frame['params']['payload']['n'] for frame in delivered] == list(range(total))
            assert list(live.subscribers.values()) == [fast.actor], 'overflowed subscriber kept its stale subscription'
            assert (await stalled.dispatch({'id': 1, 'method': 'ping', 'params': {}})).get('result') is not None
            gate.set()
            seen = []
            while True:
                frame = await asyncio.to_thread(slow.frames.get, True, 5)
                seen.append(frame['params']['type'])
                if frame['params']['type'] == 'session.replay_gap':
                    break
            assert frame['params']['session_id'] == 's'
            assert frame['params']['payload']['latest_seq'] <= live.event_stream.watermark()[1]
            assert frame['params']['payload']['replay_epoch'] == live.event_stream.watermark()[0]
            assert seen.count('session.replay_gap') == 1
            # Nothing else reaches the retired subscription until it resumes for a snapshot.
            live.event_stream.publish('s', {'n': 'after'}, event_type='message.delta')
            assert (await asyncio.to_thread(healthy.frames.get, True, 5))['params']['payload'] == {'n': 'after'}
            assert not live.event_stream.fanout.contains(slow)
            assert slow.frames.empty()
            await stalled.resume(ref, {})
            assert len(live.subscribers) == 2
            live.event_stream.publish('s', {'n': 'resumed'}, event_type='message.delta')
            assert (await asyncio.to_thread(slow.frames.get, True, 5))['params']['payload'] == {'n': 'resumed'}
        finally:
            gate.set()
            await fast.close()
            await stalled.close()
