"""Real outgoing MCP wire media is sealed and consumed without plaintext disk copies."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import secrets
import sys

import pytest

from hermes_security import artifacts, vault

pytestmark = pytest.mark.platforms("posix")
ROOT = Path(__file__).resolve().parents[2]
SERVER = '''from mcp.server import MCPServer
from mcp.types import CallToolResult,ImageContent,AudioContent,EmbeddedResource,BlobResourceContents
from PIL import Image,PngImagePlugin
from io import BytesIO
import base64
server=MCPServer("private-media")
@server.tool()
def media(canary: str) -> CallToolResult:
 b=BytesIO(); metadata=PngImagePlugin.PngInfo(); metadata.add_text("private",canary)
 Image.new("RGB",(12,8),"red").save(b,format="PNG",pnginfo=metadata)
 return CallToolResult(content=[ImageContent(type="image",data=base64.b64encode(b.getvalue()).decode(),mimeType="image/png"),
  AudioContent(type="audio",data=base64.b64encode(b"OggS"+canary.encode()).decode(),mimeType="audio/ogg"),
  EmbeddedResource(type="resource",resource=BlobResourceContents(uri="memory://private/result.txt",mimeType="text/plain",blob=base64.b64encode(canary.encode()).decode()))])
server.run(transport="stdio")
'''


def test_real_mcp_media_wire_encrypted_readers_ram_and_owner_lifetime(tmp_path, monkeypatch):
    from tools.mcp_tool import MCPServerTask
    from tools.mcp_tool_handlers import _render_content_blocks
    from tools.mcp_tool_content import _mcp_native_image_part
    from tools import file_tools, media_artifacts
    from hermes_security.errors import VaultLockedError

    home = tmp_path / "owner"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(home, "synthetic MCP media wire password")
    vault.unlock(home, "synthetic MCP media wire password")
    canary = secrets.token_hex(24)
    script = tmp_path / "public-server.py"
    script.write_text(SERVER)

    async def exchange():
        server = MCPServerTask("private-media")
        try:
            await server.start({"command": sys.executable, "args": [str(script)], "cwd": str(tmp_path),
                                "env": {"PYTHONPATH": str(ROOT)}, "connect_timeout": 15})
            return await server.session.call_tool("media", {"canary": canary})
        finally:
            await server.shutdown()

    try:
        response = asyncio.run(exchange())
        text, usable, images = _render_content_blocks(response, "private-media")
        assert usable == 3 and len(images) == 1
        assert _mcp_native_image_part(images[0])[0]["image_url"]["url"].startswith("data:image/")
        document = next((home / "artifacts/media/documents").iterdir())
        assert canary in json.loads(file_tools.read_file_tool(str(document)))["content"]
        audio = next((home / "artifacts/media/audio").iterdir())

        def consume(staged):
            assert Path(staged).read_bytes() == b"OggS" + canary.encode()
            assert Path(staged).is_relative_to(Path(media_artifacts.temporary_directory_options()["dir"]))
            return Path(staged)

        staged = media_artifacts.transcribe_owned(audio, consume)
        assert not staged.exists()
        assert "MEDIA:" in text and "saved to" in text
        assert all(canary.encode() not in p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())
        vault.lock_now(home)
        with pytest.raises(VaultLockedError):
            artifacts.read_bytes(images[0])
    finally:
        vault.clear_vault_cache()
