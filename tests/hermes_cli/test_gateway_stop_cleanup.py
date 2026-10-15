"""A profile stop never falls through to the global gateway kill path."""

from pathlib import Path
from types import SimpleNamespace

import pytest

import hermes_cli.gateway as gateway_cli


class TestGatewayStopCleanup:
    @pytest.mark.platforms("linux")
    def test_stop_only_kills_current_profile_by_default(self, tmp_path, monkeypatch):
        """Without --all, stop uses systemd (if available) and does NOT call
        the global kill_gateway_processes().

        Linux-gated: the routing under test is the systemd arm, and it is only
        reached when the host really isn't macOS/Windows (the old
        ``is_macos → False`` stub is gone).
        """
        unit_path = tmp_path / "hermes-gateway.service"
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
        unit_path.write_text("unit\n", encoding="utf-8")

        monkeypatch.setattr(gateway_cli, "supports_systemd_services", lambda: True)
        monkeypatch.setattr(gateway_cli, "get_systemd_unit_path", lambda system=False: unit_path)

        service_calls = []
        kill_calls = []

        monkeypatch.setattr(gateway_cli, "systemd_stop", lambda system=False: service_calls.append("stop"))
        monkeypatch.setattr(gateway_cli, "find_gateway_pids", lambda **_kw: [])
        monkeypatch.setattr(
            gateway_cli,
            "kill_gateway_processes",
            lambda force=False, all_profiles=False: kill_calls.append(force) or 2,
        )

        gateway_cli.gateway_command(SimpleNamespace(gateway_command="stop"))

        assert service_calls == ["stop"]
        # Global kill should NOT be called without --all
        assert kill_calls == []
