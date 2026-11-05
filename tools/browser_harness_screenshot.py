"""Private-child screenshot admission around the pinned SDK's existing image writer.

Only the isolated Hermes-owned browser worker installs this adapter. The SDK still owns
the helper signature, path, decoding, write, resize and return value. Raw CDP and shared
browser helpers retain their existing behavior.
"""
from contextvars import ContextVar
from functools import wraps
import time


class _Budget:
    def __init__(self, timeout, clock, timeout_error):
        self.clock, self.timeout_error = clock, timeout_error
        self.deadline = clock() + timeout

    def remaining(self):
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise self.timeout_error('Private screenshot exceeded its original total deadline')
        return remaining


def _bounded_request(send, request, budget, limit):
    response = send(request, response_timeout=min(limit, budget.remaining()))
    budget.remaining()
    return response


def _identity(response):
    identity = response.get('session_id'), response.get('target_id')
    if any(not isinstance(value, str) or not value for value in identity):
        raise RuntimeError('Private screenshot has no admitted current page session')
    return identity


def _capture_request(send, request, timeout, *, ordinary_timeout, timeout_error, clock=time.monotonic):
    budget = _Budget(timeout, clock, timeout_error)
    status = {'meta': 'connection_status'}
    identity = _identity(_bounded_request(send, status, budget, ordinary_timeout))
    session, _target = identity
    if request.get('session_id') not in (None, session):
        raise RuntimeError('Private screenshot cannot replace an explicitly selected page session')
    _bounded_request(send, {'method': 'Page.bringToFront', 'params': {}, 'session_id': session},
                     budget, ordinary_timeout)
    if _identity(_bounded_request(send, status, budget, ordinary_timeout)) != identity:
        raise RuntimeError('Private screenshot page changed during activation')
    response = _bounded_request(send, dict(request, session_id=session), budget, timeout)
    if _identity(_bounded_request(send, status, budget, ordinary_timeout)) != identity:
        raise RuntimeError('Private screenshot page changed before image publication')
    return response


def install_private_capture(helpers):
    """Bind only this fresh CLI child's screenshot-helper calls, not its raw CDP API."""
    active = ContextVar('hermes_private_screenshot', default=False)
    original_send, original_capture = helpers._send, helpers.capture_screenshot
    ordinary_timeout = helpers.DEFAULT_IPC_RESPONSE_TIMEOUT_SECONDS

    def send(request, response_timeout=ordinary_timeout):
        if active.get() and request.get('method') == 'Page.captureScreenshot':
            return _capture_request(original_send, request, response_timeout,
                                    ordinary_timeout=ordinary_timeout, timeout_error=helpers._IPCResponseTimeout)
        return original_send(request, response_timeout=response_timeout)

    @wraps(original_capture)
    def capture_screenshot(path=None, full=False, max_dim=None):
        token = active.set(True)
        try:
            return original_capture(path=path, full=full, max_dim=max_dim)
        finally:
            active.reset(token)

    helpers._send, helpers.capture_screenshot = send, capture_screenshot
