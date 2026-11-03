"""Invalid resources and malformed tokens cannot persist or disclose credentials."""
import pytest

from tests.tools._mcp_device_contracts import assert_device_login_failure


@pytest.mark.parametrize("mode", ["resource", "malformed"])
def test_invalid_resource_or_token_preserves_state(mode):
    assert_device_login_failure(mode)
