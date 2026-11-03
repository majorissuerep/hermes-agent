"""A preregistered client authorizes and refreshes without dynamic registration."""
from tests.tools._mcp_device_contracts import assert_device_login_success


def test_preregistered_client_authorizes_and_refreshes():
    assert_device_login_success("preregistered")
