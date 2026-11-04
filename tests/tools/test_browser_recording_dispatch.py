"""Recording cleanup keeps its original generation, profile and execution target."""
from __future__ import annotations

import pytest


def test_failed_record_stop_dispatches_once_without_session_renewal(monkeypatch):
    from tools import browser_tool as browser, browser_tool_session as session

    original = {"session_name": "original"}
    monkeypatch.setattr(browser, "_active_sessions", {"task": original})
    monkeypatch.setattr(browser, "_safe_command_timeout", lambda: 30)
    monkeypatch.setattr(session, "_get_session_info", lambda *args: pytest.fail("record stop cannot renew a session"))
    sent = []

    def dispatch(task, info, driver, command, args, timeout, engine):
        sent.append((task, info, driver, command, args))
        return engine, {"success": False, "returncode": 101, "error": "daemon stopped"}

    monkeypatch.setattr(session, "_dispatch_browser_command", dispatch)
    result = session._run_recording_command("task", original, "owned-driver", "chromium", ["stop"])
    assert result["success"] is False
    assert sent == [("task", original, "owned-driver", "record", ["stop"])]
    browser._active_sessions["task"] = {"session_name": "replacement"}
    result = session._run_recording_command("task", original, "owned-driver", "chromium", ["stop"])
    assert result["success"] is False and len(sent) == 1


def test_captured_terminal_never_acquires_a_replacement_or_crosses_profiles(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tools import browser_terminal as terminal

    first, second = (tmp_path / "first").resolve(), (tmp_path / "second").resolve()
    env = object()
    runtime = terminal.BrowserRuntime("/run/original", "/run/original/profile", "/run/original/captures",
                                      "/run/original/sockets", "/run/original/tmp")
    from dataclasses import replace
    from hermes_platform.remote_identity import RuntimeIdentity
    runtime = replace(runtime, generation=RuntimeIdentity("unit-generation", ()))
    checked = []
    monkeypatch.setattr("hermes_platform.remote_workspace.validate", lambda run, root, parts, **kwargs: checked.append(root))
    monkeypatch.setattr("hermes_platform.remote_workspace.acquire", lambda *a, **kw: pytest.fail("must not acquire"))
    monkeypatch.setattr("tools.terminal_tool._get_env_config", lambda: {"env_type": "docker"})
    owner = set_hermes_home_override(first)
    try:
        with terminal.captured_execution(first, "ssh", env, runtime):
            assert terminal.terminal_backend() == "ssh"
            assert terminal.terminal_environment(create=True) is env
            assert terminal.browser_runtime(env) is runtime
            foreign = set_hermes_home_override(second)
            try:
                with pytest.raises(RuntimeError, match="different profile"):
                    terminal.terminal_environment(create=True)
            finally:
                reset_hermes_home_override(foreign)
        assert terminal.terminal_backend() == "docker"
    finally:
        reset_hermes_home_override(owner)
    assert checked and set(checked) == {"/run/original"}
