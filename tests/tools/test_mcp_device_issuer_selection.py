"""Device login skips an invalid issuer candidate before choosing the valid issuer."""
from tests.tools._mcp_device_contracts import assert_device_login_success


def test_valid_issuer_is_selected_after_invalid_candidate():
    assert_device_login_success("multi_issuer")
