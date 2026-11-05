"""Capability admission with synthetic help/vendor responses; no Lightpanda is executed."""

import subprocess
from types import SimpleNamespace

import pytest
from tools import browser_lightpanda as lp

pytest_plugins = ["tests.tools.test_browser_lightpanda_private_state"]
pytestmark = pytest.mark.platforms("linux", "macos")
_REAL_PROBE = lp._binary_supports_http_cache


def test_command_advertised_cache_reaches_protected_vendor_argv(
    fake_lightpanda, native_state, monkeypatch, tmp_path
):
    owner, launched = fake_lightpanda
    binary = tmp_path / "selected-public-executable-identity"
    binary.write_bytes(b"public executable identity only; never executed")
    calls = []

    def help_response(argv, **kwargs):
        calls.append(list(argv))
        assert "HERMES_SESSION_VAULT" not in kwargs["env"]
        assert kwargs["env"]["LIGHTPANDA_DISABLE_TELEMETRY"] == "true"
        assert 0 < kwargs["timeout"] <= 3
        output = (
            "--http-cache-dir <PATH>"
            if argv[1:] == ["help", "serve"]
            else "Use help <command> for options"
        )
        return SimpleNamespace(stdout=output, stderr="", returncode=0)

    monkeypatch.setattr(owner, "_binary_supports_http_cache", _REAL_PROBE)
    owner._CAPABILITIES.clear()
    monkeypatch.setattr(owner, "find_lightpanda_binary", lambda: str(binary))
    monkeypatch.setattr(owner.subprocess, "run", help_response)
    monkeypatch.setattr(
        owner,
        "_browser_env",
        lambda: {"HERMES_SESSION_VAULT": "synthetic rejected authority marker"},
    )
    server, error = owner.launch_lightpanda("modern-command-help")
    assert server is not None and error is None
    assert "--http-cache-dir" in launched[0].argv
    assert launched[0].argv[launched[0].argv.index("--http-cache-dir") + 1] == str(
        server.lease.cache
    )
    assert calls == [[str(binary), "help", "serve"]]
    owner.stop_lightpanda(server.session_name, expected=server.lease.token)


def test_legacy_fallback_shares_original_three_second_budget_and_timeout_refuses(
    monkeypatch, tmp_path
):
    binary = tmp_path / "legacy-public-executable-identity"
    binary.write_bytes(b"public identity only")
    clock = [10.0]
    calls = []
    monkeypatch.setattr(lp, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    lp._CAPABILITIES.clear()

    def legacy(argv, **kwargs):
        calls.append((list(argv), kwargs["timeout"]))
        assert "HERMES_VAULT_KEY_FD" not in kwargs["env"]
        assert kwargs["env"]["LIGHTPANDA_DISABLE_TELEMETRY"] == "true"
        if argv[1:] == ["help", "serve"]:
            clock[0] += 1.25
            return SimpleNamespace(
                stdout="", stderr="older CLI has no command-specific help", returncode=1
            )
        clock[0] += 0.5
        return SimpleNamespace(stdout="--http-cache-dir PATH", stderr="", returncode=0)

    monkeypatch.setattr(lp.subprocess, "run", legacy)
    assert _REAL_PROBE(str(binary), {"HERMES_VAULT_KEY_FD": "97"})
    assert calls == [
        ([str(binary), "help", "serve"], 3.0),
        ([str(binary), "help"], 1.75),
    ]
    assert _REAL_PROBE(str(binary), {}) and len(calls) == 2
    unsupported = tmp_path / "unsupported-public-identity"
    unsupported.write_bytes(b"other public identity")
    attempted = []

    def timeout(argv, **kwargs):
        attempted.append((argv, kwargs["timeout"]))
        clock[0] += kwargs["timeout"]
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(lp.subprocess, "run", timeout)
    assert not _REAL_PROBE(str(unsupported), {})
    assert len(attempted) == 1 and attempted[0][1] == 3.0
