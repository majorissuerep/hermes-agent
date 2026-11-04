"""Fixed owner-side maintenance in the EXISTING native job lifecycle.

No caller-selected callable/executable, second job map, plaintext spool or new transport.
The native owner registers/polls/cancels this same small-interface object as NativeChild.
"""
from __future__ import annotations

import json
import threading
import time

from hermes_security.operation_custody import OperationBudget


class OwnedManagementJob:
    def __init__(self, home, operation):
        if operation not in {"browser.outgoing.repair", "browser.worker.repair"}:
            raise ValueError("unsupported fixed owning operation")
        self.home, self.operation = home, operation
        self.budget = OperationBudget(home, idle_timeout=20)
        self.finished = threading.Event()
        self.last_poll = time.monotonic()
        self.returncode, self.reason, self.result = None, None, None
        self._offset, self._encoded = 0, None
        from agent.memory_provider import spawn_context_thread
        self.worker = spawn_context_thread(self._run, name='hermes-owned-maintenance', daemon=True)

    def start(self):
        """The existing authority registry admits/registers this job BEFORE starting effects."""
        self.worker.start()

    def _run(self):
        from hermes_cli.management import execute
        try:
            with self.budget.scope():
                report = execute({'op': self.operation}, self.home)
                self.budget.check()
                self.result = {'version': 1, 'ok': not bool(report['retained']), 'result': report}
                if report['retained']:
                    self.result['error'] = {'code': 'state_refused', 'message': 'Private state was retained; inspect the result'}
                self.returncode = 1 if report['retained'] else 0
        except (RuntimeError, OSError, ValueError, TypeError):
            self.reason = 'cancelled' if self.budget.cancelled.is_set() else 'refused'
            self.returncode = 130 if self.reason == 'cancelled' else 1
            self.result = {'version': 1, 'ok': False, 'error': {'code': 'state_refused',
                           'message': 'Private maintenance did not finish; original backend RAM, recovery references and verified archives were retained'}}
        finally:
            self.finished.set()

    def poll(self):
        self.last_poll = time.monotonic()
        self.budget.last_poll = self.last_poll
        chunks = []
        if self.finished.is_set():
            if self._encoded is None:
                self._encoded = (json.dumps(self.result) + '\n').encode()
            block = self._encoded[self._offset:self._offset + 16384]
            if block:
                chunks = [('stdout', block)]
                self._offset += len(block)
        done = self.finished.is_set() and self._encoded is not None and self._offset == len(self._encoded)
        return chunks, done, self.returncode, self.reason

    def close(self):
        self.budget.cancelled.set()
        self.worker.join(timeout=3)
        if not self.finished.is_set():
            raise RuntimeError('Private maintenance cancellation is pending; its job and domain references remain tracked')
