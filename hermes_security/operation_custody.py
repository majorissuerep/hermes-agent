"""Original-source custody and cancellation for fixed owner-side maintenance jobs."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import threading
import time

class OperationCancelled(RuntimeError):
    """The bounded owning operation ended; retained data must not be mutated further."""

_CURRENT = ContextVar('owned_maintenance_budget', default=None)


class OperationBudget:
    def __init__(self, home, *, timeout=600, idle_timeout=None):
        from hermes_security.session_custody import SessionCustody
        self.operation = SessionCustody.capture(home)
        self.cancelled = threading.Event()
        self.deadline = time.monotonic() + timeout
        self.last_poll = time.monotonic()
        self.idle_timeout = idle_timeout

    def check(self):
        from tools.interrupt import is_interrupted
        now = time.monotonic()
        if (self.cancelled.is_set() or now >= self.deadline or is_interrupted()
                or self.idle_timeout is not None and now - self.last_poll >= self.idle_timeout):
            self.cancelled.set()
            raise OperationCancelled('Private maintenance cancelled or its deadline ended; existing copies retained')
        self.operation.check()

    def guarded(self, custody):
        return _GuardedCustody(custody, self)

    @contextmanager
    def scope(self):
        token = _CURRENT.set(self)
        try:
            self.check()
            yield
        finally:
            _CURRENT.reset(token)


class _GuardedCustody:
    def __init__(self, original, budget):
        self.original, self.budget = original, budget

    @property
    def home(self):
        return self.original.home

    @property
    def resource(self):
        return getattr(self.original, "resource", self.original)

    @property
    def vault(self):
        return self.original.vault

    @property
    def authority(self):
        return self.original.authority

    def check(self):
        self.budget.check()
        self.original.check()

    def derive_key(self, purpose):
        self.check()
        return self.original.derive_key(purpose)

    def check_target(self, path):
        self.check()
        self.original.check_target(path)

    def aad_path(self, relative):
        self.check()
        return self.original.aad_path(relative)

    def encrypt(self, payload, *, purpose, relpath):
        self.check()
        return self.original.encrypt(payload, purpose=purpose, relpath=relpath)

    def decrypt(self, payload, *, purpose, relpath):
        self.check()
        return self.original.decrypt(payload, purpose=purpose, relpath=relpath)


def guarded(custody):
    budget = _CURRENT.get()
    return budget.guarded(custody) if budget is not None else custody
