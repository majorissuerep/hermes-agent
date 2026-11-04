"""Actual stdio MCP registration/dispatch cannot retain a revoked operation's media.

Run on the native OS in a disposable environment with the real SDK and child process.
The server and image are synthetic; this does not qualify an external MCP account.
"""
import http.server
import json
from pathlib import Path
import secrets
import sys
import threading

import pytest

from hermes_security import artifacts, vault
from tests.tools.test_mcp_private_media import _image

pytest_plugins = ["tests.tools.test_mcp_private_media"]
pytestmark = pytest.mark.platforms("linux", "macos")
ROOT = Path(__file__).resolve().parents[2]
SERVER = '''from mcp.server import MCPServer
from mcp.types import CallToolResult, ImageContent
from PIL import Image,PngImagePlugin
from io import BytesIO
import base64,urllib.request
server=MCPServer("operation-media")
@server.tool()
def capture(canary: str, revoke_url: str) -> CallToolResult:
 if revoke_url:
  with urllib.request.urlopen(revoke_url,timeout=15) as response:
   assert response.status==200
 output,metadata=BytesIO(),PngImagePlugin.PngInfo()
 metadata.add_text("private",canary)
 Image.new("RGB",(12,8),"red").save(output,format="PNG",pnginfo=metadata)
 return CallToolResult(content=[ImageContent(type="image",mimeType="image/png",
  data=base64.b64encode(output.getvalue()).decode())])
server.run(transport="stdio")
'''


@pytest.mark.parametrize("change", ["session-close", "vault-reunlock"])
def test_real_stdio_result_keeps_original_session_and_fresh_sibling_connection_works(
        home, tmp_path, monkeypatch, change):
    from agent.session_context import scoped_current_session_id
    from agent.secret_scope import build_profile_secret_scope, reset_secret_scope, set_secret_scope
    from hermes_security.session_authority import start_session_authority
    from tools.mcp_tool_discovery import register_mcp_servers
    from tools.mcp_tool_lifecycle import shutdown_mcp_servers
    from tools.mcp_tool_schema import mcp_prefixed_tool_name
    from tools.registry import registry

    first_id, sibling_id = "wire-A-" + change, "wire-B-" + change
    first, sibling = start_session_authority(home, first_id), start_session_authority(home, sibling_id)
    callback_errors, signals, connections, connection_scopes = [], [], [], []

    class Revoke(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                if change == "session-close":
                    first.close()
                else:
                    vault.lock_now(home)
                    vault.unlock(home, "synthetic media custody password")
                signals.append(self.path)
                self.send_response(200)
            except Exception as error:
                callback_errors.append(type(error).__name__)
                self.send_response(500)
            self.end_headers()

        def log_message(self, *_args):
            return

    callback = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Revoke)
    thread = threading.Thread(target=callback.serve_forever)
    thread.start()
    script = tmp_path / "public-server.py"
    script.write_text(SERVER)
    config = {"operation-media": {"command": sys.executable, "args": [str(script)],
        "cwd": str(tmp_path), "env": {"PYTHONPATH": str(ROOT)}, "connect_timeout": 20}}
    canary = secrets.token_hex(24)

    def invoke(session_id, revoke_url):
        from tools.mcp_tool_discovery import _owner_scope_home
        from tools.mcp_tool_scope import _resolve_server_key
        from tools import mcp_tool
        from hermes_constants import get_hermes_home

        # Native sessions share the profile connection; Source/session identity
        # belongs to each request, independently of the profile registry scope.
        token = set_secret_scope(build_profile_secret_scope(home), profile_home=str(home))
        try:
            with scoped_current_session_id(session_id):
                owner = _owner_scope_home()
                assert owner is None or owner.resolve() == home.resolve()
                assert get_hermes_home().resolve() == home.resolve()
                scope = mcp_tool._mcp_registry_scope()
                assert scope in (None, str(home.resolve()))
                register_mcp_servers(config)
                connection_scopes.append(scope)
                connection = mcp_tool._servers[_resolve_server_key("operation-media")]
                if connections:
                    assert connection is connections[0]
                connections.append(connection)
                name = mcp_prefixed_tool_name("operation-media", "capture")
                assert registry.get_entry(name) is not None
                result = registry.dispatch(name, {"canary": canary, "revoke_url": revoke_url})
                return json.loads(result["text_summary"] if isinstance(result, dict) else result)
        finally:
            reset_secret_scope(token)

    try:
        result = invoke(first_id, f"http://127.0.0.1:{callback.server_port}/revoke")
        assert signals == ["/revoke"] and callback_errors == []
        assert "could not be retained" in result["result"] and "MEDIA:" not in result["result"]
        assert not [path for path in (home / "artifacts").rglob("*") if path.is_file()]
        assert vault.is_unlocked(home)
        if change == "vault-reunlock":
            sibling.close()
            sibling = start_session_authority(home, sibling_id)
        fresh = invoke(sibling_id, "")
        retained = [path for path in (home / "artifacts").rglob("*") if path.is_file()]
        assert len(retained) == 1 and str(retained[0]) in fresh["result"]
        assert artifacts.read_bytes(retained[0], home=home) == _image(canary)
        assert all(canary.encode() not in path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    finally:
        for scope in set(connection_scopes):
            shutdown_mcp_servers(scope=scope, names={"operation-media"})
        callback.shutdown()
        callback.server_close()
        thread.join(timeout=10)
        first.close()
        sibling.close()
        assert not thread.is_alive()
