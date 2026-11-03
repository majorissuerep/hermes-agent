"""Unsupported flows and issuer mismatches cannot persist credentials."""
import pytest

from tests.tools._mcp_device_contracts import assert_device_login_failure


@pytest.mark.parametrize("mode", ["unsupported", "issuer"])
def test_unsupported_flow_or_issuer_mismatch_preserves_state(mode):
    assert_device_login_failure(mode)
