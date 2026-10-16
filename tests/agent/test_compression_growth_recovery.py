"""Material appended history earns one probe; estimates, restarts and rewinds do not."""
from __future__ import annotations

import pytest

from agent.compression_growth_recovery import maybe_rearm_after_growth, record_growth_baseline
from agent.context_compressor import ContextCompressor
from hermes_state import SessionDB


def _compressor(database, session_id="retained", provider="custom"):
    compressor = ContextCompressor(model="growth-test", provider=provider, base_url="https://example.invalid/v1",
                                   config_context_length=64_000, threshold_tokens_cap=12_000, quiet_mode=True)
    compressor.bind_session_state(database, session_id)
    return compressor


def _history():
    return [{"role": "system", "content": "stable system"},
            {"role": "user", "content": "keep the task"}, {"role": "assistant", "content": "working"}]


def _append_material(history):
    return [*history, {"role": "user", "content": "new material " * 3000},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "continue"}, {"role": "assistant", "content": "ready"}]


def test_growth_probation_is_durable_bounded_and_does_not_reset_on_estimates_or_restart(tmp_path):
    with SessionDB(tmp_path / "state.db") as database:
        database.create_session("retained", source="cli")
        compressor = _compressor(database)
        history = _history()
        record_growth_baseline(compressor, history)
        compressor.record_rejected_compaction()
        compressor.record_rejected_compaction()
        assert not compressor.should_compress(20_000)
        resumed = _compressor(database)
        assert not resumed.should_compress(60_000)  # a larger estimate is not appended context
        assert not maybe_rearm_after_growth(resumed, history)
        tiny = [*history, {"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
        assert not maybe_rearm_after_growth(resumed, tiny)
        grown = _append_material(history)
        assert maybe_rearm_after_growth(resumed, grown)
        assert resumed.should_compress(30_000)
        resumed.record_rejected_compaction()
        record_growth_baseline(resumed, grown)
        assert not maybe_rearm_after_growth(_compressor(database), grown)
        assert not _compressor(database).should_compress(30_000)


@pytest.mark.parametrize("mismatch", ["session", "route", "prefix"])
def test_growth_from_another_session_route_or_rewritten_prefix_cannot_disarm_the_guard(tmp_path, mismatch):
    with SessionDB(tmp_path / "state.db") as database:
        database.create_session("retained", source="cli")
        database.create_session("other", source="cli")
        compressor = _compressor(database)
        history = _history()
        record_growth_baseline(compressor, history)
        if mismatch == "session":
            compressor = _compressor(database, session_id="other")
        elif mismatch == "route":
            compressor = _compressor(database, provider="another-route")
        compressor.record_rejected_compaction()
        compressor.record_rejected_compaction()
        grown = _append_material(history)
        if mismatch == "prefix":
            grown[0] = {"role": "system", "content": "rewritten"}
        assert not maybe_rearm_after_growth(compressor, grown)
        assert not compressor.should_compress(50_000)
