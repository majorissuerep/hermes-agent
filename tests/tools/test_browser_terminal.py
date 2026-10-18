"""The chosen execution backend never silently falls back to the native host."""
from types import SimpleNamespace

import pytest


def test_unsupported_selected_sandbox_refuses_before_host_browser_discovery(monkeypatch):
    from tools import browser_tool_session as session
    from tools import browser_terminal as target

    monkeypatch.setattr(target, "terminal_backend", lambda: "modal")
    def host_discovery():
        pytest.fail("selected sandbox fell back to host browser discovery")
    monkeypatch.setattr(session._install, "_find_agent_browser", host_discovery)
    result = session._browser_command_preflight()
    assert not result["success"]
    assert "modal" in result["error"]
    assert "browser_cmd" not in result


def test_missing_sandbox_browser_cli_refuses_with_image_prerequisite(monkeypatch):
    from tools import browser_tool_session as session
    from tools import browser_terminal as target
    from tools.environments import streams

    env = SimpleNamespace()
    monkeypatch.setattr(target, "terminal_backend", lambda: "docker")
    monkeypatch.setattr(target, "terminal_environment", lambda **_: env)
    probes = []

    def run_in(owner, argv, **kwargs):
        assert owner is env
        probes.append(argv)
        return SimpleNamespace(returncode=127)

    monkeypatch.setattr(streams, "run_in", run_in)
    monkeypatch.setattr(session._install, "_find_agent_browser", lambda: pytest.fail("host CLI discovery"))
    result = session._browser_command_preflight()
    assert result["success"] is False
    assert "agent-browser is missing inside the selected sandbox" in result["error"]
    assert "sandbox-terminal.Dockerfile" in result["error"]
    assert len(probes) == 1
    assert "browser_cmd" not in result


def test_failed_selected_terminal_command_has_no_host_fallback(monkeypatch):
    from tools import browser_tool_session as session
    from tools import browser_terminal as target
    from tools.environments import streams

    env = SimpleNamespace(get_temp_dir=lambda: "/tmp/selected-terminal")
    monkeypatch.setattr(target, "terminal_backend", lambda: "docker")
    monkeypatch.setattr(target, "terminal_environment", lambda **kwargs: env)
    monkeypatch.setattr(target, "browser_profile_dir", lambda selected: "/home/agent/browser-profile")
    monkeypatch.setattr(streams, "run_in", lambda *args, **kwargs: None)
    exported = []
    def refusing_backend(selected, command, *, child_env, interactive):
        assert selected is env and command[0] == "agent-browser"
        exported.append(child_env)
    monkeypatch.setattr(streams, "remote_command", refusing_backend)
    with pytest.raises(RuntimeError, match="cannot host browser commands"):
        session._sandbox_wrap(["agent-browser", "--session", "private", "snapshot"], {
            "OPENAI_API_KEY": "must-not-be-forwarded", "DISPLAY": ":77",
            "AGENT_BROWSER_EXECUTABLE_PATH": "/native/chromium", "PATH": "/native/bin",
        }, "/host/socket")
    assert len(exported) == 1
    assert "OPENAI_API_KEY" not in exported[0]
    assert "DISPLAY" not in exported[0]
    assert "AGENT_BROWSER_EXECUTABLE_PATH" not in exported[0]


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
        value, error = browser_tool_real_profile._launch_real_profile_chrome("/native/chrome", "/unused")
    else:
        value, error = browser_lightpanda.launch_lightpanda("private")
    assert value is None
    assert "selected terminal sandbox" in error
