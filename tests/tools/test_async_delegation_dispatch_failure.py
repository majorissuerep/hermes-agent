"""Failed dispatch preparation must not consume an unscheduled delegation slot."""
from __future__ import annotations

import copy
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from tools import async_delegation as ad


class PersistenceFailure(Exception):
    pass


class DispatchLedger:
    """Transaction seam: stage writes until commit; no actual database is opened."""

    def __init__(self):
        self.rows = {"user-history": {"state": "completed", "payload": "keep this result"}}
        self.pending = None
        self.identities = {}
        self.pending_identities = None
        self.before_delete = None
        self.phase = None
        self.failure = PersistenceFailure("injected dispatch preparation failure")
        self.replace_reservation = False
        self.replacement = None

    @contextmanager
    def transaction(self):
        self.pending = copy.deepcopy(self.rows)
        self.pending_identities = copy.deepcopy(self.identities)
        try:
            yield self
        except BaseException:
            raise
        else:
            self.rows = self.pending
            self.identities = self.pending_identities
        finally:
            self.pending = None
            self.pending_identities = None

    def execute(self, statement, parameters=()):
        if statement.lstrip().startswith("INSERT"):
            delegation_id = parameters[0]
            if self.replace_reservation:
                self.replacement = {
                    **ad._records[delegation_id],
                    "goal": "a newer reservation",
                }
                ad._records[delegation_id] = self.replacement
            if self.phase == "before_insert":
                raise self.failure
            self.pending[delegation_id] = {
                "state": "running", "payload": parameters[8],
            }
            self.pending_identities[delegation_id] = tuple(parameters)
        elif statement.lstrip().startswith("DELETE"):
            if self.before_delete is not None:
                self.before_delete()
            return self._delete(parameters)
        else:
            raise AssertionError("unexpected statement in dispatch transaction seam")

    def _delete(self, parameters):
        target = self.pending if self.pending is not None else self.rows
        identities = self.pending_identities if self.pending_identities is not None else self.identities
        delegation_id = parameters[0]
        existing = target.get(delegation_id)
        same_insert = (
            identities.get(delegation_id) == tuple(parameters)
            and existing is not None
            and existing["state"] == "running"
            and existing["payload"] == parameters[8]
        ) if len(parameters) > 1 else existing is not None
        if not same_insert:
            return SimpleNamespace(rowcount=0)
        target.pop(delegation_id)
        identities.pop(delegation_id, None)
        return SimpleNamespace(rowcount=1)

    def prune(self, *_connection):
        if self.phase == "prune_after_insert":
            raise self.failure


@pytest.fixture
def dispatch_probe(monkeypatch):
    ledger = DispatchLedger()
    records = {}
    submitted = []
    executor = SimpleNamespace(submit=lambda task: submitted.append(task))
    monkeypatch.setattr(ad, "_records", records)
    monkeypatch.setattr(ad, "_transaction", ledger.transaction)
    monkeypatch.setattr(ad, "_prune_durable_records", ledger.prune)
    monkeypatch.setattr(ad, "_get_executor", lambda _size: executor)
    monkeypatch.setattr(ad, "_capture_routing_origin", dict)
    monkeypatch.setattr(ad, "propagate_context_to_thread", lambda task: task)
    monkeypatch.setattr("hermes_platform.processes.get_process_start_time", lambda _pid: 1)
    identifiers = iter(("first", "second"))
    monkeypatch.setattr(ad, "_new_delegation_id", lambda: next(identifiers))

    def dispatch():
        def must_not_run():
            raise AssertionError("the intercepted executor must not run a child")
        return ad.dispatch_async_delegation(
            goal="inert", context=None, toolsets=None, role="leaf", model="m",
            session_key="owner", runner=must_not_run, max_async_children=1,
        )

    return SimpleNamespace(
        ledger=ledger, records=records, submitted=submitted,
        executor=executor, dispatch=dispatch,
    )


@pytest.mark.parametrize(
    ("phase", "replace_reservation"),
    [("before_insert", False), ("prune_after_insert", False), ("before_insert", True)],
)
def test_failed_persistence_is_atomic_and_releases_only_its_reservation(
    dispatch_probe, phase, replace_reservation,
):
    probe = dispatch_probe
    original_rows = copy.deepcopy(probe.ledger.rows)
    probe.ledger.phase = phase
    probe.ledger.replace_reservation = replace_reservation
    with pytest.raises(PersistenceFailure) as caught:
        probe.dispatch()
    assert caught.value is probe.ledger.failure
    assert probe.submitted == []
    assert probe.ledger.rows == original_rows
    if replace_reservation:
        assert probe.records["first"] is probe.ledger.replacement
        assert probe.dispatch()["status"] == "rejected"
    else:
        assert ad.active_count() == 0
        probe.ledger.phase = None
        assert probe.dispatch()["status"] == "dispatched"
        assert len(probe.submitted) == 1
        assert probe.ledger.rows["user-history"] == original_rows["user-history"]
        assert set(probe.ledger.rows) == {"user-history", "second"}


@pytest.mark.parametrize("failure_point", ["executor_creation", "submit"])
def test_failed_scheduling_cleans_the_prepared_dispatch(dispatch_probe, monkeypatch, failure_point):
    probe = dispatch_probe
    original_rows = copy.deepcopy(probe.ledger.rows)

    def fail(*_args):
        raise RuntimeError("injected scheduling failure")

    if failure_point == "executor_creation":
        monkeypatch.setattr(ad, "_get_executor", fail)
    else:
        probe.executor.submit = fail
    result = probe.dispatch()
    assert result["status"] == "rejected"
    assert "injected scheduling failure" in result["error"]
    assert ad.active_count() == 0
    assert probe.ledger.rows == original_rows
    assert probe.submitted == []


def test_healthy_dispatch_preserves_history_and_reserves_one_slot(dispatch_probe):
    probe = dispatch_probe
    history = copy.deepcopy(probe.ledger.rows["user-history"])
    first, second = probe.dispatch(), probe.dispatch()
    assert [first["status"], second["status"]] == ["dispatched", "rejected"]
    assert ad.active_count() == 1
    assert len(probe.submitted) == 1
    assert probe.ledger.rows["user-history"] == history
    assert set(probe.ledger.rows) == {"user-history", first["delegation_id"]}


@pytest.mark.parametrize("replacement_time", ["executor", "delete"])
def test_scheduling_failure_preserves_replaced_durable_preparation(
    dispatch_probe, monkeypatch, replacement_time,
):
    probe = dispatch_probe
    replacement_row = {"state": "running", "payload": "newer durable preparation"}
    original_record = []

    def replace_durable():
        target = probe.ledger.pending if probe.ledger.pending is not None else probe.ledger.rows
        target["first"] = replacement_row

    def fail_executor(_size):
        original_record.append(probe.records["first"])
        if replacement_time == "executor":
            replace_durable()
        else:
            probe.ledger.before_delete = replace_durable
        raise RuntimeError("injected executor failure after durable replacement")

    monkeypatch.setattr(ad, "_get_executor", fail_executor)
    error = None
    try:
        probe.dispatch()
    except RuntimeError as caught:
        error = caught
    durable_preserved = probe.ledger.rows.get("first") == replacement_row
    assert durable_preserved
    assert error is not None and "after durable replacement" in str(error)
    assert probe.records.get("first") is original_record[0]
    assert probe.submitted == []


def test_late_memory_replacement_survives_cleanup_of_the_original_durable_row(
    dispatch_probe, monkeypatch,
):
    probe = dispatch_probe
    replacement = {}

    def fail_executor(_size):
        replacement.update({**probe.records["first"], "goal": "newer memory preparation"})
        probe.ledger.before_delete = lambda: probe.records.__setitem__("first", replacement)
        raise RuntimeError("injected executor failure")

    monkeypatch.setattr(ad, "_get_executor", fail_executor)
    assert probe.dispatch()["status"] == "rejected"
    assert probe.records["first"] is replacement
    assert "first" not in probe.ledger.rows
    assert "user-history" in probe.ledger.rows
    assert probe.submitted == []


def test_scheduling_failure_preserves_changed_durable_insert_identity(dispatch_probe, monkeypatch):
    probe = dispatch_probe

    def fail_executor(_size):
        identity = list(probe.ledger.identities["first"])
        identity[5] += 1
        probe.ledger.identities["first"] = tuple(identity)
        raise RuntimeError("injected executor failure after identity change")

    monkeypatch.setattr(ad, "_get_executor", fail_executor)
    error = None
    try:
        probe.dispatch()
    except RuntimeError as caught:
        error = caught
    assert "first" in probe.ledger.rows
    assert error is not None and "after identity change" in str(error)
    assert "first" in probe.records
    assert probe.submitted == []


@pytest.mark.parametrize("failure_point", ["executor_creation", "submit"])
def test_missing_insert_receipt_preserves_the_original_scheduling_error(
    dispatch_probe, monkeypatch, failure_point,
):
    probe = dispatch_probe
    original = RuntimeError("injected failure without an insert receipt")

    def fail(*_args):
        raise original

    monkeypatch.setattr(ad, "_persist_dispatch", lambda _record: None)
    if failure_point == "executor_creation":
        monkeypatch.setattr(ad, "_get_executor", fail)
    else:
        probe.executor.submit = fail
    with pytest.raises(RuntimeError) as caught:
        probe.dispatch()
    assert caught.value is original
    assert "first" in probe.records
    assert set(probe.ledger.rows) == {"user-history"}
    assert probe.submitted == []


def test_missing_insert_receipt_keeps_healthy_dispatch_accepted(dispatch_probe, monkeypatch):
    probe = dispatch_probe
    monkeypatch.setattr(ad, "_persist_dispatch", lambda _record: None)
    assert probe.dispatch()["status"] == "dispatched"
    assert len(probe.submitted) == 1
    assert ad.active_count() == 1
    assert set(probe.ledger.rows) == {"user-history"}


def test_cleanup_failure_remains_secondary_to_the_original_scheduling_error(
    dispatch_probe, monkeypatch,
):
    probe = dispatch_probe
    original = RuntimeError("inert original submit failure")
    cleanup = PersistenceFailure("inert cleanup open failure")
    real_transaction = probe.ledger.transaction
    transactions = []

    @contextmanager
    def transaction():
        transactions.append(True)
        if len(transactions) == 2:
            raise cleanup
        with real_transaction() as conn:
            yield conn

    def fail_submit(_task):
        raise original

    monkeypatch.setattr(ad, "_transaction", transaction)
    probe.executor.submit = fail_submit
    with pytest.raises(RuntimeError) as caught:
        probe.dispatch()
    assert caught.value is original
    assert caught.value.__cause__ is cleanup
    assert "first" in probe.records and "first" in probe.ledger.rows
    assert "user-history" in probe.ledger.rows
    assert probe.submitted == []


@pytest.mark.parametrize(
    ("interrupt_type", "phase"),
    [(KeyboardInterrupt, "open"), (SystemExit, "execute"), (BaseException, "exit")],
)
def test_new_cleanup_interrupt_propagates_with_uncertain_state_retained(
    dispatch_probe, monkeypatch, interrupt_type, phase,
):
    probe = dispatch_probe
    original = RuntimeError("inert original executor failure")
    interrupt = interrupt_type("inert new cleanup interrupt")
    real_transaction = probe.ledger.transaction
    real_execute = probe.ledger.execute
    transactions = []

    @contextmanager
    def transaction():
        transactions.append(True)
        is_cleanup = len(transactions) == 2
        if is_cleanup and phase == "open":
            raise interrupt
        with real_transaction() as conn:
            yield conn
            if is_cleanup and phase == "exit":
                raise interrupt

    def execute(statement, parameters=()):
        if len(transactions) == 2 and statement.lstrip().startswith("DELETE") and phase == "execute":
            raise interrupt
        return real_execute(statement, parameters)

    def fail_executor(_size):
        raise original

    monkeypatch.setattr(ad, "_transaction", transaction)
    monkeypatch.setattr(probe.ledger, "execute", execute)
    monkeypatch.setattr(ad, "_get_executor", fail_executor)
    with pytest.raises(interrupt_type) as caught:
        probe.dispatch()
    assert caught.value is interrupt
    assert "first" in probe.records and "first" in probe.ledger.rows
    assert "user-history" in probe.ledger.rows
    assert probe.submitted == []
