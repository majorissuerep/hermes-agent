"""Pre-dispatch cancellation must remain cancellation, not an unchanged summary."""
from types import SimpleNamespace

import pytest

from agent import conversation_compression as cc
from agent.auxiliary_client import AuxiliaryExplicitCancellation


def test_cancelled_before_dispatch_raises_cancellation_without_summary_or_working_claim():
    compressor = SimpleNamespace()
    generation = cc._claim_compressor_attempt(compressor)
    owner = SimpleNamespace(context_compressor=compressor, session_id="synthetic-cancel")
    fence = cc.CompressionCommitFence()
    assert fence.cancel_before_commit()
    calls = []
    messages = [{"role": "user", "content": "unchanged synthetic context"}]

    def summary(rows, **kwargs):
        calls.append(rows)
        return rows

    with pytest.raises(AuxiliaryExplicitCancellation):
        cc._run_summary_dispatch(owner, messages, summary, {}, commit_fence=fence,
                                 attempt_generation=generation, hard_cancel_event=None)
    assert calls == []
    assert getattr(compressor, "_compression_working_attempt_generation", None) is None
    assert cc._COMPRESSOR_ATTEMPT_GENERATION.get() is None


def test_active_unchanged_summary_remains_a_real_candidate_for_structural_checks():
    compressor = SimpleNamespace()
    generation = cc._claim_compressor_attempt(compressor)
    owner = SimpleNamespace(context_compressor=compressor, session_id="synthetic-live")
    fence = cc.CompressionCommitFence()
    calls = []
    messages = [{"role": "user", "content": "unchanged synthetic context"}]

    def summary(rows, **kwargs):
        calls.append(rows)
        return rows

    result = cc._run_summary_dispatch(owner, messages, summary, {}, commit_fence=fence,
                                      attempt_generation=generation, hard_cancel_event=None)
    assert result is messages and calls == [messages]
    assert compressor._compression_working_attempt_generation == generation
    assert cc._COMPRESSOR_ATTEMPT_GENERATION.get() is None
