"""Once-per-interval counters retain their latch across transient writer contention."""

import sqlite3

from hermes_cli.observability.shared_metrics import SharedMetricsStore
from hermes_cli.observability.shared_metrics_contract import CLIENT_ACTIVE_METRIC


def test_busy_active_install_is_deferred_and_committed_once(tmp_path):
    path = tmp_path / "metrics.sqlite3"
    store = SharedMetricsStore(path, tmp_path / "outbox")
    resource = {"architecture": "x86_64", "hermes_version": "fixture",
                "install_method": "git", "os_family": "linux"}
    blocker = sqlite3.connect(path)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        assert store.record_client_active(resource) is False
        assert store.record_client_active(resource) is False
    finally:
        blocker.rollback()
        blocker.close()

    store.drain_deferred()
    [counter] = store.counter_snapshot()
    assert counter["metric_name"] == CLIENT_ACTIVE_METRIC
    assert counter["value"] == 1
    assert store.record_client_active(resource) is False
