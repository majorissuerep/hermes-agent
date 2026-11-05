"""Retained encrypted sinks refuse lost metadata and foreign redirected targets."""
from pathlib import Path

import pytest


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("damage", ["metadata-removed", "metadata-corrupt", "foreign-log-directory"])
def test_retained_log_refuses_profile_retarget_before_any_cipher_change(tmp_path, monkeypatch, damage):
    import hermes_constants as constants
    from hermes_security import frames, vault
    from hermes_security.errors import VaultError
    from tools import mcp_tool_config as config

    parent, child, sibling = tmp_path / "parent", tmp_path / "parent/profiles/child", tmp_path / "sibling"
    monkeypatch.setenv("HERMES_HOME", str(parent))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    monkeypatch.setattr(config, "_mcp_stderr_log_fh", {})
    monkeypatch.setattr(constants, "_HOME_KEY_CACHE", {})
    for home in (parent, child, sibling):
        vault.init_vault(home, "synthetic independent log review password")
        vault.unlock(home, "synthetic independent log review password")
    alias = tmp_path / "selected-alias"
    alias.symlink_to(child, target_is_directory=True)
    token = constants.set_hermes_home_override(alias)
    try:
        sink = config._get_mcp_stderr_log()
        sink.write("original selected child record\n")
        physical = child / "logs/mcp-stderr.log"
        original = physical.read_bytes()
        if damage == "metadata-removed":
            (child / ".hermes-vault").unlink()
        elif damage == "metadata-corrupt":
            (child / ".hermes-vault").write_bytes(b"{not valid vault metadata")
        else:
            foreign = sibling / "logs/mcp-stderr.log"
            frames.append(foreign, b"original foreign profile record\n", purpose="log")
            foreign_before = foreign.read_bytes()
            retained = child / "verified-original-logs"
            physical.parent.rename(retained)
            physical.parent.symlink_to(foreign.parent, target_is_directory=True)
            physical = retained / physical.name
        with pytest.raises((VaultError, PermissionError)):
            sink.write("must never adopt another profile or damaged store\n")
        assert physical.read_bytes() == original
        if damage != "foreign-log-directory":
            with pytest.raises((VaultError, PermissionError)):
                config._get_mcp_stderr_log()
            assert not sink.closed and list(config._mcp_stderr_log_fh.values()) == [sink]
        else:
            assert foreign.read_bytes() == foreign_before
            assert b"original foreign profile record" in b"".join(frames.read_frames(foreign, purpose="log"))
        assert vault.is_unlocked(parent) and vault.is_unlocked(sibling)
    finally:
        config._close_mcp_stderr_logs()
        constants.reset_hermes_home_override(token)
        vault.clear_vault_cache()
