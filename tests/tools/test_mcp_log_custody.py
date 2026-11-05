"""Retained profile log belongs to one unlock, independently of first Source."""

from pathlib import Path
import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    from hermes_security import vault

    selected = tmp_path / "owner"
    selected.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(selected))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(selected, "synthetic media custody password")
    vault.unlock(selected, "synthetic media custody password")
    yield selected
    vault.clear_vault_cache()


@pytest.mark.parametrize("reunlock", [False, True])
def test_retained_mcp_sink_keeps_original_unlock_but_not_first_source(home, reunlock):
    from hermes_security import artifacts, frames, vault
    from hermes_security.errors import VaultError
    from hermes_security.session_authority import start_session_authority
    from tools.mcp_tool_config import _VaultMCPLog

    source = start_session_authority(home, "original-log-first")
    path = Path(home) / "logs/mcp-stderr.log"
    sink = _VaultMCPLog(path, custody=artifacts._bound_custody(home))
    sink.write("first synthetic line\n")
    before = path.read_bytes()
    source.close()
    if reunlock:
        vault.lock_now(home)
        vault.unlock(home, "synthetic media custody password")
        with pytest.raises(VaultError):
            sink.write("late old-unlock synthetic line\n")
        assert path.read_bytes() == before
        fresh = _VaultMCPLog(path, custody=artifacts._bound_custody(home))
        fresh.write("fresh synthetic line\n")
        assert b"fresh synthetic line" in b"".join(
            frames.read_frames(path, purpose="log")
        )
    else:
        sink.write("shared profile still live\n")
        assert b"shared profile still live" in b"".join(
            frames.read_frames(path, purpose="log")
        )
    sink.close()


def test_fresh_log_factory_replaces_original_unlock_without_reusing_stale_handle(
    home, monkeypatch
):
    from hermes_security import frames, vault
    from tools import mcp_tool_config as config

    monkeypatch.setattr(config, "_mcp_stderr_log_fh", {})
    old = config._get_mcp_stderr_log()
    old.write("original factory line\n")
    vault.lock_now(home)
    vault.unlock(home, "synthetic media custody password")
    fresh = config._get_mcp_stderr_log()
    try:
        assert fresh is not old and old.closed
        fresh.write("new factory line\n")
        payload = b"".join(frames.read_frames(fresh.name, purpose="log"))
        assert b"original factory line" in payload and b"new factory line" in payload
    finally:
        config._close_mcp_stderr_logs()
