"""Built-in ``cua`` computer-use provider: cua-driver over MCP (:mod:`tools.computer_use.cua_backend`)."""

from __future__ import annotations

import sys

from tools.computer_use.backend import ComputerUseBackend, ComputerUseProvider


class CuaDriverProvider(ComputerUseProvider):
    name = "cua"
    display_name = "cua-driver (background)"

    def create_backend(self, *, permission_mode: str) -> ComputerUseBackend:
        from tools.computer_use.cua_backend import CuaDriverBackend
        return CuaDriverBackend(permission_mode=permission_mode)

    def is_available(self) -> bool:
        """macOS/Windows/Linux + cua-driver binary (or env override). `hermes computer-use doctor` names blocked checks."""
        if sys.platform not in ("darwin", "win32", "linux"):
            return False
        from tools.browser_terminal import terminal_backend
        if terminal_backend() != "local":
            return False
        from tools.computer_use.cua_backend_driver import cua_driver_binary_available
        return cua_driver_binary_available()


def register(ctx):
    ctx.register_computer_use_provider(CuaDriverProvider())
