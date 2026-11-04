"""Fixed owner WORKER repair contracts with real custody/locks and a declared unit RAM seam.

Actual logged-in public CLI/native-job routing is qualified separately in disposable guests.
"""
from contextlib import contextmanager
import contextvars
from pathlib import Path
import threading

import pytest

pytest_plugins = ['tests.tools.test_browser_native_private_state']
pytestmark = pytest.mark.platforms('linux', 'macos')


def test_owner_worker_repair_preserves_shared_profile_while_recovering_closed_source(native_state):
    from agent.session_context import scoped_current_session_id
    from hermes_cli import browser_worker_store as store
    from hermes_cli.browser_worker_repair import repair_owned
    from hermes_security import browser_worker_archive as archive, vault
    from hermes_security.operation_custody import OperationBudget
    from hermes_security.session_authority import start_session_authority
    from hermes_state.runtime_ownership import ProfileOwnership

    home, _user, ram = native_state
    first = start_session_authority(home, 'worker-departed-A')
    sibling = start_session_authority(home, 'worker-repair-B')
    other = start_session_authority(home, 'worker-independent-C')
    resident = ProfileOwnership()
    resident.reserve([home])
    selected = store.resource(home, kind='worker-original-operation')
    try:
        with scoped_current_session_id('worker-departed-A'):
            with selected.operation():
                (selected.tree/'progress').write_bytes(b'original committed private progress')
            with pytest.raises(store.WorkerStateError):
                with selected.operation():
                    (selected.tree/'progress').write_bytes(b'latest retained private progress')
                    first.close()
        previous = (home/selected.relative).read_bytes()
        with scoped_current_session_id('worker-repair-B'):
            selected.release()  # the actual producer is no longer active; its resident owner remains
            with OperationBudget(home).scope():
                report = repair_owned(home)
        assert report == {'saved':[selected.relative.as_posix()], 'retained':[]}
        assert not selected.path.exists() and (home/selected.relative).read_bytes() != previous
        readback = ram/'explicit-verified-recovery'
        readback.mkdir(mode=0o700)
        archive.replay_archive(home, selected.relative, custody=archive.capture_custody(home), target=readback)
        assert (readback/'progress').read_bytes() == b'latest retained private progress'
        assert vault.is_unlocked(home) and sibling.is_live() and other.is_live() and resident.owns(home)
    finally:
        resident.close()
        first.close()
        sibling.close()
        other.close()


def test_owner_worker_repair_cancellation_does_not_wait_for_busy_resource_or_change_state(native_state, monkeypatch):
    from hermes_cli import browser_worker_store as store
    from hermes_cli.browser_worker_repair import repair_owned
    from hermes_security.operation_custody import OperationBudget, OperationCancelled

    home, _user, _ram = native_state
    selected = store.resource(home, kind='worker-busy-cancel')
    with selected.operation():
        (selected.tree/'progress').write_bytes(b'preserve busy private progress')
    selected.release()
    cipher = (home/selected.relative).read_bytes()
    held, release, entering, finished = [threading.Event() for _ in range(4)]
    failures = []
    real_lock = store._lock
    @contextmanager
    def observed_lock(path, **kwargs):
        if threading.current_thread().name == 'owned-worker-repair':
            entering.set()
        with real_lock(path, **kwargs):
            yield
    monkeypatch.setattr(store, '_lock', observed_lock)
    def holder():
        with real_lock(selected.lock_path):
            held.set()
            assert release.wait(10)
    thread = threading.Thread(target=holder)
    thread.start()
    assert held.wait(5)
    budget = OperationBudget(home)
    def repairing():
        try:
            with budget.scope():
                repair_owned(home)
        except BaseException as exc:
            failures.append(exc)
        finally:
            finished.set()
    context = contextvars.copy_context()
    worker = threading.Thread(target=lambda:context.run(repairing), name='owned-worker-repair')
    worker.start()
    try:
        assert entering.wait(5)
        budget.cancelled.set()
        assert finished.wait(5), 'Cancelled repair waited for the unrelated resource owner'
        assert len(failures) == 1 and isinstance(failures[0], OperationCancelled)
        assert (selected.tree/'progress').read_bytes() == b'preserve busy private progress'
        assert (home/selected.relative).read_bytes() == cipher
    finally:
        budget.cancelled.set()
        release.set()
        thread.join(timeout=5)
        worker.join(timeout=5)
        assert not thread.is_alive() and not worker.is_alive()
