"""A real stdio MCP peer that records its synthetic profile-scoped child environment."""

from __future__ import annotations

import sys
from pathlib import Path

SECRET_ENV = "SCOPE_CANARY_SECRET"
MCP_ENV_KEY = "SCOPE_MCP_OWN_SECRET"
MCP_SERVER_NAME = "envdump"

# A stdio MCP server that records os.environ at spawn into the file named by argv[1], then serves
# one trivial tool so the client's handshake completes like any real server's.
_ENV_DUMP_SERVER = '''
import json, os, sys

out = sys.argv[1]
tmp = out + ".part"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(dict(os.environ), fh)
os.replace(tmp, out)

from mcp.server import MCPServer

server = MCPServer("envdump")


@server.tool()
def envdump_ping(nonce: str = "") -> str:
    """Echo the nonce."""
    return nonce


server.run(transport="stdio")
'''


def write_env_dump_server(path: Path) -> Path:
    path.write_text(_ENV_DUMP_SERVER, encoding="utf-8")
    return path


def mcp_config(server_script: Path, dump_file: Path) -> dict:
    """``mcp_servers`` entry: the profile passes exactly one secret, by reference to its own .env."""
    return {MCP_SERVER_NAME: {
        "command": sys.executable,
        "args": [str(server_script), str(dump_file)],
        "env": {MCP_ENV_KEY: "${" + SECRET_ENV + "}"},
    }}
