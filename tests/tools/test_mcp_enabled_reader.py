"""Every reader of ``mcp_servers.<name>.enabled`` gives one answer per value.

The MCP client, the toolset resolver, the catalog and the list/status
surfaces used to parse the key four different ways, so ``enabled: 0`` was off for the resolver
and on for the client, and ``enabled: "false"`` was on in the server list. The table covers the supported native readers and legacy saved values.
"""

from __future__ import annotations


import pytest

CASES = [{'enabled': True, 'on': True}, {'enabled': False, 'on': False}, {'enabled': 1, 'on': True}, {'enabled': 0, 'on': False}, {'enabled': 0.0, 'on': False}, {'enabled': 2, 'on': True}, {'enabled': -0.0, 'on': False}, {'enabled': [], 'on': True}, {'enabled': 'true', 'on': True}, {'enabled': 'Yes', 'on': True}, {'enabled': ' on ', 'on': True}, {'enabled': '1', 'on': True}, {'enabled': 'false', 'on': False}, {'enabled': 'OFF', 'on': False}, {'enabled': 'no', 'on': False}, {'enabled': '0', 'on': False}, {'enabled': '', 'on': True}, {'enabled': 'maybe', 'on': True}, {'enabled': None, 'on': True}, {'on': True}]


def _readers():
    from hermes_cli.mcp_catalog import server_enabled
    from hermes_cli.tools_config import enabled_mcp_server_names
    from tools.mcp_tool_common import mcp_server_enabled
    from tui_runtime.mcp_rpc_helpers import summarize_server

    return {
        "mcp client": mcp_server_enabled,
        "toolset resolver": lambda cfg: "s" in enabled_mcp_server_names({"mcp_servers": {"s": cfg}}),
        "catalog": server_enabled,
        "server list": lambda cfg: summarize_server("s", cfg)["enabled"],
    }


@pytest.mark.parametrize("case", CASES, ids=lambda c: repr(c.get("enabled", "<absent>")))
def test_every_reader_agrees_with_the_shared_table(case):
    entry = {k: v for k, v in case.items() if k != "on"}
    answers = {name: bool(read({"command": "x", **entry})) for name, read in _readers().items()}

    assert answers == dict.fromkeys(answers, case["on"])
