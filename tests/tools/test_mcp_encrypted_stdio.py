"""Real stdio MCP keeps its profile, filtered authority and encrypted diagnostics."""
import asyncio
import json
from pathlib import Path
import secrets
import subprocess
import sys

import pytest

from agent.secret_scope import build_profile_secret_scope, reset_secret_scope, set_secret_scope
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from hermes_security import frames, io as state_io, vault

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.platforms("posix")


@pytest.fixture(params=[False, True], ids=["owning-home", "explicit-server-home"])
def stdio_result(tmp_path, monkeypatch, request):
    from tools.mcp_tool import MCPServerTask
    from tools.mcp_tool_lifecycle import shutdown_mcp_servers

    launch, profile, os_home = (tmp_path / name for name in ("launch", "profile", "os-home"))
    os_home.mkdir()
    password = secrets.token_urlsafe(24)
    for home in (launch, profile):
        vault.init_vault(home, password)
        vault.unlock(home, password)
    vault.lock_now(launch)
    monkeypatch.setenv("HOME", str(os_home))
    monkeypatch.setenv("USERPROFILE", str(os_home))
    monkeypatch.setenv("HERMES_HOME", str(launch))
    monkeypatch.setenv("HERMES_SESSION_VAULT", "must-not-be-forwarded")
    monkeypatch.setenv("HERMES_VAULT_KEY_FD", "9999")
    monkeypatch.setenv("HERMES_MASTER_PASSWORD", "must-not-be-forwarded")
    monkeypatch.setenv("FOREIGN_PROFILE_SECRET", "launch-only")
    home_token = set_hermes_home_override(profile)
    secret = "owned-" + secrets.token_hex(24)
    state_io.write_text(profile / ".env", f"MCP_PROBE_SECRET={secret}\n", purpose="env")
    scope_token = set_secret_scope(build_profile_secret_scope(profile), profile_home=str(profile))
    server = tmp_path / "server.py"
    server.write_text(
        "import json,os,sys\n"
        "from mcp.server import MCPServer\n"
        "from hermes_constants import get_hermes_home\n"
        "print('MCP_DIAGNOSTIC_WITNESS '+os.environ['MCP_PROBE_SECRET'],file=sys.stderr,flush=True)\n"
        "server=MCPServer('encrypted-scope')\n"
        "@server.tool()\n"
        "def probe() -> str:\n"
        " return json.dumps({'home':str(get_hermes_home()),'unexpected':[k for k in "
        "['HERMES_SESSION_VAULT','HERMES_VAULT_KEY_FD','HERMES_MASTER_PASSWORD','FOREIGN_PROFILE_SECRET'] if k in os.environ]})\n"
        "server.run(transport='stdio')\n", encoding="utf-8")
    expected_home = tmp_path / "explicit-server-home" if request.param else profile
    server_env = {"PYTHONPATH": str(ROOT), "MCP_PROBE_SECRET": secret}
    if request.param:
        server_env["HERMES_HOME"] = str(expected_home)

    async def run():
        task = MCPServerTask("encrypted-scope")
        try:
            await task.start({"command": sys.executable, "args": [str(server)], "cwd": str(tmp_path),
                              "env": server_env,
                              "connect_timeout": 15})
            result = await task.session.call_tool("probe", {})
            return json.loads(result.content[0].text)
        finally:
            await task.shutdown()

    try:
        result = asyncio.run(run())
        yield result, profile, secret, expected_home
    finally:
        shutdown_mcp_servers()
        reset_secret_scope(scope_token)
        reset_hermes_home_override(home_token)
        vault.clear_vault_cache()


def test_real_stdio_server_uses_owning_home_without_unlock_material(stdio_result):
    result, _, _, expected_home = stdio_result
    assert result["home"] == str(expected_home)
    assert result["unexpected"] == []


def test_real_stdio_diagnostics_remain_encrypted_and_redacted(stdio_result):
    _, profile, secret, _ = stdio_result
    path = profile / "logs" / "mcp-stderr.log"
    raw = path.read_bytes()
    assert b"MCP_DIAGNOSTIC_WITNESS" not in raw
    assert secret.encode() not in raw
    text = b"".join(frames.read_frames(path, purpose="log")).decode()
    assert "MCP_DIAGNOSTIC_WITNESS" in text
    assert secret not in text


def test_stdio_log_write_failure_is_reported_without_a_substitute(tmp_path):
    from tools.mcp_tool_config import _StderrTee, _close_mcp_stderr_logs, _get_mcp_stderr_log

    home_token = set_hermes_home_override(tmp_path)
    password = secrets.token_urlsafe(24)
    vault.init_vault(tmp_path, password)
    vault.unlock(tmp_path, password)
    try:
        log = _get_mcp_stderr_log()
        path = Path(log.name)
        path.unlink()
        path.mkdir()  # An actual filesystem write failure after the stream opened.
        tee = _StderrTee(log)
        subprocess.run([sys.executable, "-c", "import sys; sys.stderr.write('diagnostic\\n')"],
                       stderr=tee.sink, check=True, timeout=10)
        with pytest.raises(OSError):
            tee.close()
        assert path.is_dir()
    finally:
        _close_mcp_stderr_logs()
        reset_hermes_home_override(home_token)
        vault.clear_vault_cache()


def test_legacy_log_requires_repair_and_retains_diagnostics(tmp_path):
    from hermes_security.errors import VaultIntegrityError
    from hermes_security.migrate import repair_clobbered_state
    from tools.mcp_tool_config import _close_mcp_stderr_logs, _get_mcp_stderr_log

    token = set_hermes_home_override(tmp_path)
    password = secrets.token_urlsafe(24)
    vault.init_vault(tmp_path, password)
    vault.unlock(tmp_path, password)
    path = tmp_path / "logs" / "mcp-stderr.log"
    path.parent.mkdir()
    original = b"retained legacy diagnostic\n"
    path.write_bytes(original)
    try:
        with pytest.raises(VaultIntegrityError, match="secure-vault repair"):
            _get_mcp_stderr_log()
        assert path.read_bytes() == original
        report = repair_clobbered_state(tmp_path)
        assert report["unrecoverable"] == []
        log = _get_mcp_stderr_log()
        log.write("new encrypted diagnostic\n")
        text = b"".join(frames.read_frames(path, purpose="log"))
        assert original in text and b"new encrypted diagnostic" in text
        assert b"diagnostic" not in path.read_bytes()
    finally:
        _close_mcp_stderr_logs()
        reset_hermes_home_override(token)
        vault.clear_vault_cache()
