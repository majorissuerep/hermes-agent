"""Public cache footers use sandbox paths; private outputs keep owner-local reads.

The native read_file gate decrypts private delegation output before dispatching
ordinary paths to an outgoing terminal backend. No vault authority is forwarded.
"""

import os
import json
import re

from tools import browser_tool_snapshot
from tools import delegate_tool_results
from tools import web_tools_truncate
from hermes_security import artifacts
from tests.e2e.vault_home import seal_fixture_home


def _footer_path(text: str) -> str:
    """Every truncation footer tells the agent how to page the full text via ``read_file path="..."``."""
    return re.search(r'read_file path="([^"]+)"', text).group(1)


def test_truncation_footers_keep_public_sandbox_and_private_owner_paths(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    home.mkdir()
    seal_fixture_home(home)
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    body = "\n".join(f"row {i}" for i in range(5000))

    web_out, _ = web_tools_truncate._truncate_with_footer(body, "https://example.com/doc", 3000)
    snap_out = browser_tool_snapshot._truncate_snapshot(body, 3000)
    deleg_out, _ = delegate_tool_results._trim_summary_with_footer(body, 3000, 0)

    for text, subdir in ((web_out, "cache/web"), (snap_out, "cache/web"), (deleg_out, "cache/delegation")):
        path = _footer_path(text)
        expected = str(home / subdir) if subdir == "cache/delegation" else f"/root/.hermes/{subdir}"
        assert path.startswith(expected + "/"), path
        # Public cache paths are translated; encrypted output is served by the local owner.
        stored = home / subdir / os.path.basename(path)
        assert stored.is_file()
        if subdir == "cache/delegation":
            assert body.encode() not in stored.read_bytes()
            assert body in artifacts.read_text(stored, home=home)
            from tools.file_tools import read_file_tool
            assert json.loads(read_file_tool(path, offset=1, limit=1))["content"] == "1|row 0"


def test_local_backend_footer_keeps_the_host_path(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    home.mkdir()
    seal_fixture_home(home)
    monkeypatch.setenv("TERMINAL_ENV", "local")
    body = "\n".join(f"row {i}" for i in range(5000))
    out, _ = web_tools_truncate._truncate_with_footer(body, "https://example.com/doc", 3000)
    assert _footer_path(out).startswith(str(home))
