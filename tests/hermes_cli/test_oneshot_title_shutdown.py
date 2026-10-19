"""Finite owners settle their own title writes before closing the session store."""
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from agent import title_generator, turn_context
from hermes_cli import oneshot


@pytest.mark.parametrize("deferred", [False, True])
def test_close_waits_for_started_owned_title_and_not_another_agent(monkeypatch, deferred):
    events = []

    class Store:
        closed = False

        def close(self):
            events.append("db-close")
            self.closed = True

    store = Store()
    releases = [Event(), Event()]

    class Writer(Thread):
        def __init__(self, index):
            super().__init__(target=lambda: (releases[index].wait(), events.append(
                "write" if index == 0 and not store.closed else "other-or-late-write")))
            self.index = index

        def join(self, timeout=None):
            assert timeout is None, "owned title writes must finish, not outlive a join timeout"
            events.append(f"join-{self.index}")
            releases[self.index].set()
            return super().join(timeout)

    owned, other = Writer(0), Writer(1)
    if not deferred:
        owned.start()
    other.start()
    pending = iter([owned, other])
    monkeypatch.setattr(title_generator, "maybe_auto_title", lambda *args, **kwargs: next(pending))
    monkeypatch.setattr(oneshot, "_linger_for_background_completions", lambda: None)

    def agent():
        return SimpleNamespace(_session_db=store, _session_db_created=True, session_id="owned", platform="cli",
                               shutdown_memory_provider=lambda *args: None, close=lambda: events.append("agent-close"))

    owner, unrelated = agent(), agent()
    try:
        turn_context._maybe_title_session_at_turn_start(owner, [{"role": "user", "content": "a title"}])
        turn_context._maybe_title_session_at_turn_start(unrelated, [{"role": "user", "content": "another title"}])
        if deferred:
            turn_context.start_deferred_title_upgrade(owner)
        oneshot._close_agent(owner, store)
        assert "write" in events and events.index("write") < events.index("db-close"), events
        assert "join-0" in events and "join-1" not in events, events
    finally:
        releases[0].set()
        releases[1].set()
        Thread.join(owned, 5)
        Thread.join(other, 5)
        assert not owned.is_alive() and not other.is_alive()
        title_generator._UPGRADE_THREADS.discard(owned)
        title_generator._UPGRADE_THREADS.discard(other)


def test_run_agent_captures_owned_title_ledger_before_store_close(monkeypatch):
    release = Event()
    rows = {}

    class Store:
        closed = False

        def auxiliary_usage_by_task(self, session_id):
            assert not self.closed
            return dict(rows)

        def close(self):
            self.closed = True

    store = Store()

    class Writer(Thread):
        def join(self, timeout=None):
            assert timeout is None, "ledger capture cannot race a timed-out title writer"
            release.set()
            return super().join(timeout)

    def write_usage():
        release.wait()
        assert not store.closed
        rows["title_generation"] = {"api_calls": 1, "input_tokens": 7, "output_tokens": 1}

    writer = Writer(target=write_usage)

    class Agent:
        def __init__(self, **kwargs):
            self._session_db, self._session_db_created = store, True
            self.session_id, self.platform = "owned", "cli"

        def run_conversation(self, prompt, conversation_history=None):
            writer.start()
            turn_context._maybe_title_session_at_turn_start(self, [{"role": "user", "content": prompt}])
            return {"session_id": "owned", "final_response": "answer"}

        def shutdown_memory_provider(self, *args):
            pass

        def close(self):
            pass

    monkeypatch.setattr(title_generator, "maybe_auto_title", lambda *args, **kwargs: writer)
    monkeypatch.setattr(oneshot, "_create_session_db_for_oneshot", lambda: store)
    monkeypatch.setattr(oneshot, "_resolve_model_and_provider", lambda *args: oneshot._ModelChoice("model", "custom"))
    monkeypatch.setattr(oneshot, "_linger_for_background_completions", lambda: None)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"model": {"default": "model"}})
    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_with_fallback", lambda *args, **kwargs: ({
        "provider": "custom", "api_key": "synthetic", "base_url": "http://unused.invalid/v1"}, None))
    monkeypatch.setattr("hermes_cli.tools_config._get_platform_tools", lambda *args: [])
    monkeypatch.setattr("hermes_cli.mcp_startup.ensure_mcp_discovery_before_agent_build", lambda **kwargs: None)
    monkeypatch.setattr("hermes_cli.lifecycle.finalize_session", lambda **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent", Agent)
    try:
        text, result = oneshot._run_agent("a title", ledger=True)
        assert text == "answer" and store.closed
        assert result["auxiliary_usage"]["title_generation"]["api_calls"] == 1
    finally:
        release.set()
        if writer.ident is not None:
            Thread.join(writer, 5)
            assert not writer.is_alive()
