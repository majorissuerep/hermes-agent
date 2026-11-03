"""Public device authorization preserves registration, polling and refresh contracts."""
from tests.tools._mcp_device_contracts import assert_device_login_success


def test_device_login_registers_authorizes_and_persists():
    assert_device_login_success("success")
