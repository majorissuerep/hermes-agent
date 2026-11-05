"""The optional native Lightpanda engine has no Windows executable resolution."""
import pytest


@pytest.mark.platforms('windows')
def test_none_on_windows():
    from tools.browser_lightpanda import find_lightpanda_binary

    assert find_lightpanda_binary() is None
