"""Native browser/computer launchers cannot bypass an explicitly selected backend."""
import pytest


def test_native_computer_use_refuses_a_selected_sandbox(monkeypatch):
    from tools import browser_terminal as target
    from tools.computer_use.cua_backend import cua_driver_child_env

    monkeypatch.setattr(target, "terminal_backend", lambda: "ssh")
    with pytest.raises(RuntimeError, match="refusing to control the host"):
        cua_driver_child_env({"DISPLAY": ":77"})


@pytest.mark.parametrize("engine", ["profile", "lightpanda"])
def test_native_browser_launchers_refuse_selected_sandbox_before_host_spawn(monkeypatch, engine):
    from tools import browser_terminal as target
    from tools import browser_lightpanda, browser_tool_real_profile

    monkeypatch.setattr(target, "terminal_backend", lambda: "ssh")
    def unexpected_spawn(*args, **kwargs):
        pytest.fail("native browser launched outside the chosen sandbox")
    monkeypatch.setattr(browser_lightpanda.subprocess, "Popen", unexpected_spawn)
    if engine == "profile":
        value, error = browser_tool_real_profile._launch_real_profile_chrome("/native/chrome", "/unused", "chrome")
    else:
        value, error = browser_lightpanda.launch_lightpanda("private")
    assert value is None
    assert "selected terminal sandbox" in error
