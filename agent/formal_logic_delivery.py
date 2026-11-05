"""Hold native formal candidates in existing response memory until accepted delivery.

Provider collectors already retain the complete response. Do not duplicate its stream,
write a spool, or present unchecked commentary as proof or stakeholder attention.
"""
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
import threading

_DELIVERY = ContextVar('formal_verified_delivery', default=None)
_STEPS = frozenset({'stream', 'display', 'speech', 'tui-text', 'record', 'hook-text'})


@dataclass
class _Delivery:
    agent: object
    state: object
    text: str
    authority: object
    active: bool = True
    used: set = field(default_factory=set)
    lock: object = field(default_factory=threading.Lock)


def _live(agent, current):
    from agent.formal_logic import _interruption_feedback

    if _interruption_feedback(agent, current) is not None:
        return False
    home = getattr(current.authority, 'home', None)
    if home is not None:
        from hermes_constants import get_hermes_home

        if get_hermes_home().resolve() != Path(home).resolve():
            current.approved, current.finalized, current.accepted_response = False, False, ''
            current.pending = {}
            current.status = 'original_home_changed'
            current.last_receipt = {'status': current.status}
            return False
    return True


def withheld(agent, *, text=None, step=None):
    """Only the exact checked text may consume each accepted presentation step once."""
    from agent.formal_logic import state

    current = state(agent)
    if current is None:
        return False
    delivery = _DELIVERY.get()
    if (delivery is None or not delivery.active or delivery.agent is not agent
            or delivery.state is not current or delivery.authority is not current.authority
            or delivery.text != current.accepted_response or text != delivery.text
            or step not in _STEPS or not current.finalized or not _live(agent, current)):
        return True
    with delivery.lock:
        if not delivery.active or step in delivery.used:
            return True
        delivery.used.add(step)
    return False


def release_accepted(agent):
    """Send the final checked answer once through the existing display/TTS fan-out."""
    from agent.formal_logic import state

    current = state(agent)
    if (current is None or not current.finalized or current.delivered
            or not _live(agent, current)):
        return False
    emit = getattr(agent, '_fire_stream_delta', None)
    if not callable(emit):
        return False
    current.delivered = True  # Reentrant callbacks cannot replay the accepted response.
    agent._stream_needs_break = False  # No unchecked prose was displayed before this final.
    delivery = _Delivery(agent, current, current.accepted_response, current.authority)
    token = _DELIVERY.set(delivery)
    try:
        emit(delivery.text)
    finally:
        delivery.active = False  # A copied callback context cannot authorize a later write.
        _DELIVERY.reset(token)
    return True


def mark_candidate(agent, message):
    """Stamp only a newly built model/feedback row; replay content stays untouched."""
    from agent.formal_logic import state

    if state(agent) is not None:
        message['display_kind'] = 'hidden'
        message['display_metadata'] = {**(message.get('display_metadata') or {}),
                                       'formal_logic': 'candidate'}
    return message


def mark_reviewed(message, *, status="reviewed"):
    """The clean visible answer must not project its raw provider/reasoning sidecars."""
    message['display_metadata'] = {**(message.get('display_metadata') or {}),
                                   'formal_logic': status}
