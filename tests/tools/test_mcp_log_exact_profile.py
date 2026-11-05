"""MCP diagnostics require the selected profile's own usable encrypted store."""

from pathlib import Path

import pytest

pytest_plugins = ["tests.tools.test_mcp_log_custody"]


@pytest.mark.parametrize(
    "case",
    [
        "own-vault",
        "missing-isolated",
        "missing-nested",
        "locked-nested",
        "corrupt-nested",
    ],
)
def test_mcp_log_requires_exact_profile_before_creating_any_output(
    home, tmp_path, monkeypatch, case
):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from hermes_security import frames, vault
    from hermes_security.errors import VaultError
    from tools import mcp_tool_config as config

    selected = (
        home
        if case == "own-vault"
        else (
            tmp_path / "missing-owner"
            if case == "missing-isolated"
            else home / "profiles" / "child"
        )
    )
    selected.mkdir(parents=True, exist_ok=True)
    if case in {"locked-nested", "corrupt-nested"}:
        vault.init_vault(selected, "synthetic child log password")
        vault.lock_now(selected)
        if case == "corrupt-nested":
            (selected / ".hermes-vault").write_bytes(b"{broken metadata")
    token = set_hermes_home_override(selected)
    monkeypatch.setattr(config, "_mcp_stderr_log_fh", {})
    path = selected / "logs/mcp-stderr.log"
    try:
        if case == "own-vault":
            sink = config._get_mcp_stderr_log()
            sink.write("selected profile diagnostic\n")
            assert sink._custody.vault.home == home
            assert b"selected profile diagnostic" not in path.read_bytes()
            assert b"selected profile diagnostic" in b"".join(
                frames.read_frames(path, purpose="log")
            )
        else:
            with pytest.raises(VaultError):
                config._get_mcp_stderr_log()
            assert not path.parent.exists()
            assert not config._mcp_stderr_log_fh
        assert vault.is_unlocked(home)
    finally:
        config._close_mcp_stderr_logs()
        reset_hermes_home_override(token)


@pytest.mark.platforms("posix")
def test_retargeted_home_alias_selects_fresh_physical_profile_and_preserves_old_sink(
    home, tmp_path, monkeypatch
):
    import hermes_constants as constants
    from hermes_security import frames, vault
    from tools import mcp_tool_config as config

    sibling = tmp_path / "sibling"
    vault.init_vault(sibling, "synthetic sibling log password")
    vault.unlock(sibling, "synthetic sibling log password")
    alias = tmp_path / "selected-alias"
    alias.symlink_to(home, target_is_directory=True)
    monkeypatch.setattr(config, "_mcp_stderr_log_fh", {})
    monkeypatch.setattr(constants, "_HOME_KEY_CACHE", {})
    token = constants.set_hermes_home_override(alias)
    try:
        original = config._get_mcp_stderr_log()
        original.write("original profile\n")
        before = Path(original.name).read_bytes()
        alias.unlink()
        alias.symlink_to(sibling, target_is_directory=True)
        selected = config._get_mcp_stderr_log()
        assert selected is not original and not original.closed
        assert Path(original.name) == home / "logs/mcp-stderr.log"
        assert Path(selected.name) == sibling / "logs/mcp-stderr.log"
        selected.write("new selected profile\n")
        assert Path(original.name).read_bytes() == before
        original.write("old sibling remains usable\n")
        assert b"new selected profile" in b"".join(
            frames.read_frames(selected.name, purpose="log")
        )
        assert b"old sibling remains usable" in b"".join(
            frames.read_frames(original.name, purpose="log")
        )
        assert vault.is_unlocked(home) and vault.is_unlocked(sibling)
    finally:
        config._close_mcp_stderr_logs()
        constants.reset_hermes_home_override(token)
