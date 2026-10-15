"""The model picker shows subscription usage before the wall, without ever waiting on a usage API.

Usage windows come from the provider's API, so ``model.options`` must read them from cache and
refresh in the background: a slow or hung usage endpoint can't be allowed to stall the picker.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime, timedelta, UTC

import pytest

from agent import account_usage
from agent.account_usage import AccountUsageSnapshot, AccountUsageWindow
from agent.account_usage_cache import cached_account_usage
from hermes_cli.inventory import build_model_options_payload, load_picker_context
from tests.waiting import wait_until


def _snapshot(used: float) -> AccountUsageSnapshot:
    reset = datetime.now(UTC) + timedelta(hours=2)
    return AccountUsageSnapshot(
        provider="ai-gateway", source="test", fetched_at=datetime.now(UTC),
        windows=(AccountUsageWindow(label="Current session", used_percent=used, reset_at=reset),))


def test_picker_reads_cached_usage_and_refreshes_without_waiting(monkeypatch):
    from hermes_cli import model_catalog

    def unexpected_catalog_fetch(*_args, **_kwargs):
        pytest.fail("usage-cache contract unexpectedly fetched the remote model catalog")

    monkeypatch.setattr(model_catalog, "_fetch_manifest", unexpected_catalog_fetch)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "test-key-123")
    release, called = threading.Event(), threading.Event()
    answers = iter([_snapshot(92.0)])

    def slow_usage_api(_base_url, _api_key):
        called.set()
        assert release.wait(30), "test did not release the usage request"
        return next(answers)

    monkeypatch.setitem(account_usage._USAGE_FETCHERS, "ai-gateway", slow_usage_api)
    ctx = load_picker_context()

    with ThreadPoolExecutor(max_workers=1) as executor:
        picker = executor.submit(copy_context().run, build_model_options_payload, ctx)
        try:
            assert called.wait(10), "opening the picker must start a background usage refresh"
            first = next(r for r in picker.result(timeout=10)["providers"] if r["slug"] == "ai-gateway")
            assert "usage" not in first, "the picker must return while the usage request is still blocked"
        finally:
            release.set()
    wait_until(lambda: cached_account_usage("ai-gateway"), what="the released usage refresh to populate its cache")

    row = next(r for r in build_model_options_payload(ctx)["providers"] if r["slug"] == "ai-gateway")
    (window,) = row["usage"]["windows"]
    assert window["label"] == "Current session" and window["used_percent"] == 92.0
    assert datetime.fromisoformat(window["resets_at"]) > datetime.now(UTC)
