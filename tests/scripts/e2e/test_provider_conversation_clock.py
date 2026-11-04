"""The provider-wire fixture remains deterministic across a real clock rollover."""
from datetime import UTC, datetime

import hermes_time
from tests.scripts.e2e import _provider_conversation as probe


def test_provider_fixture_keeps_its_clock_when_midnight_passes(tmp_path, monkeypatch):
    current = [datetime(2026, 11, 3, 23, 59, 59, tzinfo=UTC)]
    requests = []
    original = probe._respond

    def reply(record, mode):
        requests.append(record["body"])
        current[0] = datetime(2026, 11, 4, 0, 0, 1, tzinfo=UTC)
        return original(record, mode)

    monkeypatch.setattr(hermes_time, "now", lambda: current[0])
    monkeypatch.setattr(probe, "_respond", reply)
    probe.assert_growing_conversation(tmp_path, "xai", "chat_completions", True)
    systems = [record["messages"][0]["content"] for record in requests]
    assert systems and all(system == systems[0] for system in systems)
