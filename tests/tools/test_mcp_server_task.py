"""Mocked MCP task lifecycle: diagnostics stay in memory, real logging is tested separately."""
import asyncio
import io
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mcp_tool(name="read_file", description="Read a file", input_schema=None):
    """Create a fake MCP Tool object matching the SDK interface."""
    tool = SimpleNamespace()
    tool.name = name
    tool.description = description
    tool.inputSchema = input_schema or {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path"},
        },
        "required": ["path"],
    }
    return tool


class TestMCPServerTask:
    """Test the MCPServerTask lifecycle with mocked MCP SDK."""

    @pytest.fixture(autouse=True)
    def _mocked_process_dependencies(self, monkeypatch):
        # The SDK lifecycle is mocked; real encrypted diagnostics have separate stdio contracts.
        log = io.StringIO()
        monkeypatch.setattr("tools.mcp_tool_config._managed_launcher", lambda command: None)
        monkeypatch.setattr("tools.mcp_tool_config._get_mcp_stderr_log", lambda: log)
        yield
        log.close()

    def _mock_stdio_and_session(self, session):
        """Return patches for stdio_client and ClientSession as async CMs."""
        mock_read, mock_write = MagicMock(), MagicMock()

        mock_stdio_cm = MagicMock()
        mock_stdio_cm.__aenter__ = AsyncMock(return_value=(mock_read, mock_write))
        mock_stdio_cm.__aexit__ = AsyncMock(return_value=False)

        mock_cs_cm = MagicMock()
        mock_cs_cm.__aenter__ = AsyncMock(return_value=session)
        mock_cs_cm.__aexit__ = AsyncMock(return_value=False)

        return (
            patch("tools.mcp_tool.stdio_client", return_value=mock_stdio_cm),
            patch("tools.mcp_tool.ClientSession", return_value=mock_cs_cm),
            mock_read, mock_write,
        )

    def test_start_connects_and_discovers_tools(self):
        """start() creates a Task that connects, discovers tools, and waits."""
        from tools.mcp_tool import MCPServerTask

        mock_tools = [_make_mcp_tool("echo")]
        mock_session = MagicMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(
            return_value=SimpleNamespace(tools=mock_tools)
        )

        p_stdio, p_cs, _, _ = self._mock_stdio_and_session(mock_session)

        async def _test():
            with patch("tools.mcp_tool.StdioServerParameters") as params, p_stdio, p_cs:
                server = MCPServerTask("test_srv")
                await server.start(
                    {"command": "npx", "args": ["-y", "test"], "cwd": "/plugin"}
                )

                assert server.session is mock_session
                assert len(server._tools) == 1
                assert server._tools[0].name == "echo"
                mock_session.initialize.assert_called_once()
                assert params.call_args.kwargs["cwd"] == "/plugin"

                await server.shutdown()
                assert server.session is None

        asyncio.run(_test())

    def test_start_preserves_native_default_cwd(self):
        from tools.mcp_tool import MCPServerTask

        mock_session = MagicMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=SimpleNamespace(tools=[]))
        p_stdio, p_cs, _, _ = self._mock_stdio_and_session(mock_session)

        async def _test():
            with patch("tools.mcp_tool.StdioServerParameters") as params, p_stdio, p_cs:
                server = MCPServerTask("native")
                await server.start({"command": "npx", "args": ["-y", "test"]})
                assert params.call_args.kwargs["cwd"] is None
                await server.shutdown()

        asyncio.run(_test())

    def test_start_defaults_stdio_cwd_to_session_cwd(self, tmp_path, monkeypatch):
        """A pinned session working directory becomes the stdio default cwd.

        Hosted/multiplexed sessions (ACP, gateway) pin their logical cwd; a stdio
        server spawned there inherits the Hermes process dir instead, so
        relative-path servers resolve against the wrong tree.
        """
        from agent.runtime_cwd import clear_session_cwd, set_session_cwd
        from tools.mcp_tool import MCPServerTask

        monkeypatch.delenv("TERMINAL_CWD", raising=False)
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        mock_session = MagicMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=SimpleNamespace(tools=[]))
        p_stdio, p_cs, _, _ = self._mock_stdio_and_session(mock_session)

        async def _test():
            set_session_cwd(str(workspace))
            try:
                with patch("tools.mcp_tool.StdioServerParameters") as params, p_stdio, p_cs:
                    server = MCPServerTask("session_cwd")
                    await server.start({"command": "npx", "args": ["-y", "test"]})
                    assert Path(params.call_args.kwargs["cwd"]) == workspace
                    await server.shutdown()
            finally:
                clear_session_cwd()

        asyncio.run(_test())

    def test_start_configured_cwd_overrides_session_cwd(self, tmp_path, monkeypatch):
        """An explicit per-server `cwd` in config always wins over the session anchor."""
        from agent.runtime_cwd import clear_session_cwd, set_session_cwd
        from tools.mcp_tool import MCPServerTask

        monkeypatch.delenv("TERMINAL_CWD", raising=False)
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        mock_session = MagicMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=SimpleNamespace(tools=[]))
        p_stdio, p_cs, _, _ = self._mock_stdio_and_session(mock_session)

        async def _test():
            set_session_cwd(str(workspace))
            try:
                with patch("tools.mcp_tool.StdioServerParameters") as params, p_stdio, p_cs:
                    server = MCPServerTask("explicit_wins")
                    await server.start({"command": "npx", "args": ["-y", "test"], "cwd": "/plugin"})
                    assert params.call_args.kwargs["cwd"] == "/plugin"
                    await server.shutdown()
            finally:
                clear_session_cwd()

        asyncio.run(_test())

    def test_stdio_recycle_deadline_pauses_while_rpc_active(self):
        from tools.mcp_tool import MCPServerTask

        async def _test():
            server = MCPServerTask("srv")
            server._config = {"command": "npx"}
            server._idle_timeout_seconds = 0.01
            server._last_tool_call_at = time.monotonic() - 1.0

            async with server._rpc_lock:
                assert server._stdio_recycle_reason() is None
                assert server._next_stdio_recycle_deadline() is None

        asyncio.run(_test())
