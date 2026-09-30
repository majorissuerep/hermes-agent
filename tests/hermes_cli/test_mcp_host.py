"""An agent's locked terminal child manages MCP through the authenticated, unlocked host.

Real CLI subprocesses, rendezvous discovery, ASGI/WebSocket transport, profile-scoped
RPC handlers, encrypted writers and stdio MCP discovery; no config or transport mocks.
"""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest


@pytest.fixture
def host(tmp_path, monkeypatch):
    from hermes_security import io, vault

    root = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("HERMES_GATEWAY_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    vault.init_vault(root, "host-password")
    vault.unlock(root, "host-password")
    work = root / "profiles" / "work"
    vault.init_vault(work, "work-password")
    vault.unlock(work, "work-password")
    for home in (root, work):
        io.write_text(
            home / "config.yaml",
            "# preserve my settings\ndisplay:\n  skin: default\n",
            purpose="config",
        )

    from agent.secret_scope import is_multiplex_active, set_multiplex_active
    from gateway import host_rendezvous as hr
    from hermes_cli import web_server
    from tui_gateway import server
    import uvicorn

    was_multiplex = is_multiplex_active()
    set_multiplex_active(True)
    monkeypatch.setattr(server, "_hermes_home", root)
    monkeypatch.setattr(server, "_HERMES_HOME_AT_IMPORT", root)
    token = "mcp-host-integration-token"
    monkeypatch.setattr(web_server, "_SESSION_TOKEN", token)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    backend = uvicorn.Server(
        uvicorn.Config(web_server.app, lifespan="off", log_level="error")
    )
    from agent.memory_provider import spawn_context_thread

    thread = spawn_context_thread(
        target=backend.run,
        name="mcp-host-test",
        kwargs={"sockets": [sock]},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 15
    while not backend.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert backend.started
    assert hr.publish_record(
        hr.ROLE_SERVE,
        host="127.0.0.1",
        port=port,
        token=token,
        home=str(root),
        profiles=["default", "work"],
    )

    def cli(*argv, profile="default", stdin=None, expected=0):
        env = dict(os.environ)
        env["HERMES_HOME"] = str(root)
        for key in (
            "HERMES_ALLOW_NO_VAULT",
            "HERMES_VAULT_PRIVATE_KEY",
            "HERMES_VAULT_KEY_FD",
        ):
            env.pop(key, None)
        result = subprocess.run(
            [sys.executable, "-m", "hermes_cli.main", "-p", profile, "mcp", *argv],
            input=stdin or "",
            env=env,
            capture_output=True,
            encoding="utf-8",
            timeout=45,
        )
        probe_log = (
            (root if profile == "default" else root / "profiles" / profile)
            / "logs"
            / "mcp-stderr.log"
        )
        detail = probe_log.read_text(encoding="utf-8") if probe_log.is_file() else ""
        assert result.returncode == expected, (result.stdout, result.stderr, detail)
        if expected == 0:
            return json.loads(result.stdout)
        return result

    try:
        yield root, work, cli
    finally:
        backend.should_exit = True
        thread.join(timeout=15)
        sock.close()
        set_multiplex_active(was_multiplex)
        vault.clear_vault_cache()


def test_locked_child_roundtrip_is_encrypted_profile_isolated_and_cache_safe(
    host, tmp_path
):
    from hermes_security import io
    from tui_gateway import server

    root, work, cli = host
    mcp_script = tmp_path / "mcp_server.py"
    mcp_script.write_text(
        "import asyncio, os, sys\n"
        "from mcp.server import MCPServer\n"
        "assert os.environ['SERVICE_TOKEN'] == 'test-secret-' + sys.argv[1]\n"
        "mcp = MCPServer('probe')\n"
        "@mcp.tool()\n"
        "def ping() -> str:\n    return 'pong'\n"
        "asyncio.run(mcp.run_stdio_async())\n",
        encoding="utf-8",
    )
    prompt = "byte-stable prompt"
    tools = [{"function": {"name": "existing"}}]
    from types import SimpleNamespace

    agent = SimpleNamespace(system_prompt=prompt, tools=tools)
    server._sessions["mcp-host-cache"] = {"agent": agent, "profile_home": str(work)}
    try:
        for profile, home, sibling in (("default", root, work), ("work", work, root)):
            before = (home / "config.yaml").read_bytes()
            other_before = (sibling / "config.yaml").read_bytes()
            if profile == "default":
                added = cli(
                    "add",
                    "demo",
                    "--via-host",
                    "--command",
                    sys.executable,
                    "--env",
                    "SERVICE_TOKEN=${SERVICE_TOKEN}",
                    "--args",
                    str(mcp_script),
                    profile,
                    profile=profile,
                )
            else:
                config = {
                    "command": sys.executable,
                    "args": [str(mcp_script), profile],
                    "env": {"SERVICE_TOKEN": "${SERVICE_TOKEN}"},
                }
                added = cli(
                    "add",
                    "demo",
                    "--via-host",
                    "--config-json",
                    "-",
                    stdin=json.dumps(config),
                    profile=profile,
                )
            assert added["ok"] and added["activation"] == "next_session"
            assert (home / "config.yaml").read_bytes() != before
            secret = "test-secret-" + profile
            keyed = cli(
                "set-api-key",
                "demo",
                "--via-host",
                "--env-var",
                "SERVICE_TOKEN",
                "--value-stdin",
                stdin=secret + "\n",
                profile=profile,
            )
            assert keyed["ok"] and secret not in json.dumps(keyed)
            assert (sibling / "config.yaml").read_bytes() == other_before

        # Same-named servers stay configured together; A → B → A proves isolation.
        for profile, home, sibling in (
            ("default", root, work),
            ("work", work, root),
            ("default", root, work),
        ):
            secret = "test-secret-" + profile
            listed = cli("ls", "--via-host", profile=profile)
            assert [s["name"] for s in listed["servers"]] == ["demo"]
            probed = cli("test", "demo", "--via-host", profile=profile)
            assert probed["ok"] and [t["name"] for t in probed["tools"]] == ["ping"]
            for path, purpose in (
                (home / "config.yaml", "config"),
                (home / ".env", "env"),
            ):
                assert path.read_bytes().startswith(b"HRMVAULT\x00")
                assert secret.encode() not in path.read_bytes()
                text = io.read_text(path, purpose=purpose)
                if purpose == "config":
                    assert "# preserve my settings" in text and "skin: default" in text
                    assert secret not in text and "${SERVICE_TOKEN}" in text
                else:
                    assert secret in text
            assert secret not in io.read_text(sibling / ".env", purpose="env")

        work_before = (work / "config.yaml").read_bytes()
        assert cli("rm", "demo", "--via-host")["removed"]
        assert (work / "config.yaml").read_bytes() == work_before
        assert cli("list", "--via-host")["servers"] == []
        assert cli("remove", "demo", "--via-host", profile="work")["removed"]
        assert cli("list", "--via-host", profile="work")["servers"] == []

        # HTTP templates travel as structured data, never through a plaintext file.
        http = cli(
            "add",
            "http-demo",
            "--via-host",
            "--url",
            "https://example.invalid/mcp",
            "--header",
            "Authorization=Bearer ${MCP_HTTP_DEMO_API_KEY}",
        )
        assert http["server"]["auth"] == "header"
        assert cli(
            "set-api-key", "http-demo", "--value-stdin", stdin="opaque-test-token\n"
        )["ok"]
        assert "opaque-test-token" not in io.read_text(
            root / "config.yaml", purpose="config"
        )
        assert agent.system_prompt == prompt and agent.tools is tools
        assert os.environ.get("SERVICE_TOKEN") is None
    finally:
        server._sessions.pop("mcp-host-cache", None)


def test_host_failures_never_fall_back_to_direct_writes_or_report_success(
    host, tmp_path
):
    from gateway import host_rendezvous as hr
    from hermes_security import vault

    root, work, cli = host
    original = (work / "config.yaml").read_bytes()
    direct = cli("add", "direct", "--command", "npx", profile="work", expected=2)
    assert "vault is locked" in direct.stderr
    assert (work / "config.yaml").read_bytes() == original
    # Invalid input, duplicates, blocked executable shapes and a failed real probe.
    for argv in (
        ("add", "bad", "--via-host", "--config-json", "[1]"),
        (
            "add",
            "bad",
            "--via-host",
            "--url",
            "https://example.invalid/mcp",
            "--command",
            "npx",
        ),
        (
            "add",
            "bad",
            "--via-host",
            "--command",
            "sh",
            "--args",
            "-c",
            "curl https://example.invalid",
        ),
    ):
        result = cli(*argv, profile="work", expected=2 if "--args" not in argv else 1)
        assert json.loads(result.stdout)["ok"] is False
        assert (work / "config.yaml").read_bytes() == original

    cli(
        "add",
        "demo",
        "--via-host",
        "--command",
        "hermes-mcp-missing-executable",
        profile="work",
    )
    saved = (work / "config.yaml").read_bytes()
    cli("add", "demo", "--via-host", "--command", "npx", profile="work", expected=1)
    assert (work / "config.yaml").read_bytes() == saved
    failed = cli("test", "demo", "--via-host", profile="work", expected=1)
    assert not json.loads(failed.stdout)["ok"]

    # A host with a locked target home must refuse; the client has no key.
    vault.lock_now(work)
    failed = cli(
        "add", "locked", "--via-host", "--command", "npx", profile="work", expected=1
    )
    assert not json.loads(failed.stdout)["ok"]
    assert (work / "config.yaml").read_bytes() == saved

    # A valid token for a host serving another root is not authority to write ours.
    hr.publish_record(
        hr.ROLE_SERVE,
        host="127.0.0.1",
        port=hr.read_record(hr.ROLE_SERVE).port,
        token=hr.read_token(hr.ROLE_SERVE),
        home=str(tmp_path / "other"),
    )
    cli("list", "--via-host", expected=1)
    hr.record_path(hr.ROLE_SERVE).unlink()
    absent = cli("list", "--via-host", expected=1)
    assert "host" in json.loads(absent.stdout)["error"].lower()
    assert (work / "config.yaml").read_bytes() == saved
