"""Source updates on the encrypted fork never subscribe to upstream build records."""

import pytest

from hermes_cli import source_releases


@pytest.mark.parametrize("channel", ["main", "stable", "canary"])
def test_fork_channel_stays_on_its_own_main_without_reading_release_service(monkeypatch, channel):
    monkeypatch.setattr(source_releases, "_resolve_channel", lambda *_: pytest.fail("queried upstream releases"))
    if channel != "main":
        with pytest.raises(ValueError, match="own main branch"):
            source_releases.resolve_source_target(channel, repository="majorissuerep/hermes-agent")
    else:
        target = source_releases.resolve_source_target(channel, repository="majorissuerep/hermes-agent")
        assert target.repository == "majorissuerep/hermes-agent"
        assert target.branch == "main" and target.commit is None
