"""Denied and expired device authorization preserve encrypted state."""
import pytest

from tests.tools._mcp_device_contracts import assert_device_login_failure


@pytest.mark.parametrize("mode", ["denied", "expiry"])
def test_denial_or_expiry_preserves_state(mode):
    assert_device_login_failure(mode)
