"""Native compaction updates accounting without inventing assistant transcript text."""
import pytest

from agent.transports.codex_app_server_session import CodexAppServerSession, TurnResult
from agent.transports.codex_event_projector import CodexEventProjector


@pytest.mark.parametrize("method", ["item/started", "item/completed", "thread/compacted"])
def test_native_compaction_preserves_accounting_and_only_projects_real_answer(method, tmp_path):
    session = CodexAppServerSession(cwd=str(tmp_path))
    projector = CodexEventProjector()
    previous = {"role": "assistant", "content": "prior frozen answer"}
    result = TurnResult(projected_messages=[previous.copy()])
    session._absorb_notification(result, projector, {
        "method": "item/completed", "params": {"item": {"type": "reasoning", "summary": ["pending thought"]}}})
    boundary = {"method": method, "params": {
        "threadId": "owned-thread", "turnId": "owned-turn",
        "item": {"type": "contextCompaction", "id": "boundary"}}}
    projection, aborted = session._absorb_notification(result, projector, boundary)
    assert result.compacted and result.thread_id == "owned-thread" and result.turn_id == "owned-turn"
    assert not aborted and not result.interrupted and result.error is None
    assert projection.messages == [] and projection.final_text is None
    assert result.projected_messages == [previous] and result.tool_iterations == 0
    session._absorb_notification(result, projector, {
        "method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "after compaction"}}})
    assert result.projected_messages == [previous, {
        "role": "assistant", "content": "after compaction", "reasoning": "pending thought"}]
    assert result.final_text == "after compaction"
