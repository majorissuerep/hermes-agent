"""Public caches map to the task backend; private outputs use authenticated host file tools."""

import os
import re
import pytest

from tools import browser_tool_snapshot
from tools import delegate_tool_results
from tools import web_tools_truncate


@pytest.fixture
def owner(tmp_path, monkeypatch):
    from hermes_security import vault

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(home, "synthetic footer password")
    vault.unlock(home, "synthetic footer password")
    yield home
    vault.clear_vault_cache()


def _footer_path(text: str) -> str:
    """Every truncation footer tells the agent how to page the full text via ``read_file path="..."``."""
    return re.search(r'read_file path="([^"]+)"', text).group(1)


def test_truncation_footers_render_public_cache_and_private_recovery_paths(owner, monkeypatch):
    home = owner
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    body = "\n".join(f"row {i}" for i in range(5000))

    web_out, _ = web_tools_truncate._truncate_with_footer(body, "https://example.com/doc", 3000)
    snap_out = browser_tool_snapshot._truncate_snapshot(body, 3000)
    deleg_out, _ = delegate_tool_results._trim_summary_with_footer(body, 3000, 0)

    path = _footer_path(web_out)
    assert path.startswith("/root/.hermes/cache/web/"), path
    # Public bytes remain mounted at the task backend's path.
    assert os.path.exists(str(home / "cache" / "web" / os.path.basename(path)))
    for text, subdir in ((snap_out, "artifacts/browser-snapshots"), (deleg_out, "cache/delegation")):
        path = _footer_path(text)
        assert path.startswith(str(home / subdir)), path
        assert os.path.exists(path)


def test_local_backend_footer_keeps_the_host_path(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TERMINAL_ENV", "local")
    body = "\n".join(f"row {i}" for i in range(5000))
    out, _ = web_tools_truncate._truncate_with_footer(body, "https://example.com/doc", 3000)
    assert _footer_path(out).startswith(str(home))
