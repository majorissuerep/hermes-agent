"""A failed token write preserves the existing encrypted token and metadata."""
from tests.tools._mcp_device_contracts import assert_device_login_failure


def test_failed_token_write_preserves_existing_encrypted_state():
    assert_device_login_failure("persistence")
