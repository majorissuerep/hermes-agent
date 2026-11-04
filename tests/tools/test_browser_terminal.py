"""The chosen execution backend never silently falls back to the native host, and the browser it
runs keeps its private profile, captures, sockets and temporary files in that backend's proven RAM.

Backend facts come from ``tests/tools/_protected_backend.py`` (only the exec transport is faked);
the guard and client-capture cases run real processes on native Linux.
"""
import os
import re
import subprocess
from types import SimpleNamespace

import pytest

from tests.tools._protected_backend import ACTIVE_SWAP, FakeBackend


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


@pytest.fixture
def selected(monkeypatch):
    """Select a docker backend served by ``FakeBackend``; returns an installer."""
    def install(backend):
        from tools import browser_terminal as target
        from tools.environments import streams
        monkeypatch.setattr(target, "terminal_backend", lambda: "docker")
        monkeypatch.setattr(target, "terminal_environment", lambda **_: backend)
        monkeypatch.setattr(streams, "run_in", backend.run_in)
        return backend
    return install


_HOST_ENV = {
    "OPENAI_API_KEY": "must-not-be-forwarded", "HERMES_SESSION_VAULT": "grant-must-stay-home",
    "HERMES_VAULT_KEY_FD": "7", "DISPLAY": ":77", "AGENT_BROWSER_EXECUTABLE_PATH": "/native/chromium",
    "AGENT_BROWSER_PROFILE": "/host/profile", "TMPDIR": "/host/tmp", "PATH": "/native/bin",
}


def test_failed_selected_terminal_command_has_no_host_fallback(monkeypatch, selected):
    from tools import browser_tool_session as session
    from tools.environments import streams

    backend = selected(FakeBackend([("/tmp", "tmpfs", "rw,noswap")]))
    exported = []
    def refusing_backend(target, command, *, child_env, interactive, expected_prefix):
        assert target is backend and command[-1] == "snapshot"
        exported.append(child_env)
    monkeypatch.setattr(streams, "remote_command", refusing_backend)
    with pytest.raises(RuntimeError, match="cannot host browser commands"):
        session._sandbox_wrap(["agent-browser", "--session", "private", "snapshot"], dict(_HOST_ENV), "/host/socket")
    assert len(exported) == 1
    assert {"OPENAI_API_KEY", "HERMES_SESSION_VAULT", "HERMES_VAULT_KEY_FD", "DISPLAY",
            "AGENT_BROWSER_EXECUTABLE_PATH"}.isdisjoint(exported[0])


def test_sandbox_browser_state_lives_in_the_backends_proven_ram(monkeypatch, selected):
    from tools import browser_tool_session as session
    from tools.environments import streams

    backend = selected(FakeBackend([("/tmp", "tmpfs", "rw,noswap")]))
    wrapped = []
    monkeypatch.setattr(streams, "remote_command",
                        lambda target, command, *, child_env, interactive, expected_prefix: wrapped.append(child_env) or ["client"])
    argv, client_env = session._sandbox_wrap(["agent-browser", "--session", "s", "snapshot"], dict(_HOST_ENV), "/h")
    remote = wrapped[0]
    root = backend.probes("provision")[0][8]
    assert root.startswith("/tmp/hermes-browser-") and backend.dirs[root] == "1000:700"
    assert {remote[k] for k in ("AGENT_BROWSER_PROFILE", "AGENT_BROWSER_SOCKET_DIR", "TMPDIR")} == {
        f"{root}/profile", f"{root}/sockets", f"{root}/tmp"}
    assert set(remote) <= set(session._SANDBOX_ENV_KEYS)
    assert argv == ["client"] and set(client_env) == {"PATH", "HOME"}


def test_unproven_backend_ram_refuses_the_browser_with_its_identity_before_any_spawn(monkeypatch, selected):
    from tools import browser_tool_session as session

    backend = selected(FakeBackend([("/tmp", "tmpfs", "rw,size=512m")], swaps=ACTIVE_SWAP))
    monkeypatch.setattr(session._install, "_find_agent_browser", lambda: pytest.fail("host CLI discovery"))
    monkeypatch.setattr(session, "_run_agent_browser_client", lambda *a, **k: pytest.fail("browser client spawned"))
    result = session._run_browser_command("task", "snapshot", [])
    assert result["success"] is False
    assert "docker terminal backend" in result["error"]
    assert "tmpfs pages can be written to active swap" in result["error"]
    assert "noswap" in result["error"]
    assert backend.probes("provision") == [] and backend.dirs == {}


@pytest.mark.parametrize("succeeded", [True, False])
def test_sandbox_capture_reaches_the_host_stage_and_leaves_no_backend_copy(monkeypatch, selected, tmp_path,
                                                                          succeeded):
    from tools import browser_tool

    backend = selected(FakeBackend([("/tmp", "tmpfs", "rw,noswap")]))
    backend.files["/home/agent/elsewhere.png"] = b"not a capture"
    requested = []

    def run_browser(_task_id, command, args, **_kwargs):
        assert command == "screenshot"
        requested.append(args[-1])
        backend.files[args[-1]] = b"private pixels"
        if not succeeded:
            return {"success": False, "error": "renderer crashed"}
        return {"success": True, "data": {"path": "/home/agent/elsewhere.png"}}
    monkeypatch.setattr(browser_tool._session, "_run_browser_command", run_browser)
    staged = tmp_path / "ram-stage" / "browser_screenshot_x.png"
    staged.parent.mkdir()

    _result, error = browser_tool._capture_vision_screenshot("task", False, staged, False)
    captures = backend.probes("provision")[0][8] + "/captures/"
    assert requested[0].startswith(captures)
    assert backend.files == {"/home/agent/elsewhere.png": b"not a capture"}
    if succeeded:
        assert error is None and staged.read_bytes() == b"private pixels"
    else:
        assert error is not None and not staged.exists()


def test_a_vanished_backend_runtime_requires_recovery_without_replaying_the_command(monkeypatch, selected):
    from tools import browser_terminal as target
    from tools import browser_tool_session as session
    from tools.environments import streams

    backend = selected(FakeBackend([("/tmp", "tmpfs", "rw,noswap")]))
    # This transport-only fake cannot hold an authenticated browser profile. The
    # owned-profile admission/continuity contract is exercised with real vaults
    # in test_browser_outgoing_continuity; retain this one-shot missing-runtime oracle.
    from tools import browser_outgoing_profile
    monkeypatch.setattr(browser_outgoing_profile, "prepare", lambda *_args: None)
    monkeypatch.setattr(streams, "remote_command", lambda _e, argv, *, child_env, interactive, expected_prefix: ["client", *argv])
    clients = []
    def failed_client(argv, *_args):
        clients.append(argv)
        return target.RUNTIME_MISSING_EXIT, "", target.RUNTIME_MISSING_MESSAGE + "\n"
    monkeypatch.setattr(session, "_run_agent_browser_client", failed_client)
    result = session._collect_in_sandbox("task", {"session_name": "h_1"}, ["agent-browser", "--json", "get", "title"],
                                         "get", 10, {"PATH": "/bin"}, "/host/socket", None)
    assert not result["success"] and "not run" in result["error"]
    assert len(clients) == 1 and len(backend.probes("provision")) == 1


def test_cached_runtime_rechecks_mount_and_permissions_without_replacing_profile(selected):
    from tools import browser_terminal as target

    backend = selected(FakeBackend([("/tmp", "tmpfs", "rw,noswap")]))
    runtime = target.browser_runtime(backend)
    backend.files[runtime.profile + "/Cookies"] = b"existing-private-state"
    backend.mounts = [("/tmp", "ext4", "rw")]
    with pytest.raises(RuntimeError, match="RAM-only"):
        target.browser_runtime(backend)
    assert backend.files[runtime.profile + "/Cookies"] == b"existing-private-state"
    assert len(backend.probes("provision")) == 1
    backend.mounts = [("/tmp", "tmpfs", "rw,noswap")]
    backend.dirs[runtime.profile] = "1000:750"
    with pytest.raises(RuntimeError, match="0700"):
        target.browser_runtime(backend)
    assert backend.dirs[runtime.profile] == "1000:750"


@pytest.mark.platforms("linux")
def test_runtime_guard_refuses_a_missing_or_replaced_runtime_directory(tmp_path):
    from tools.browser_terminal import BrowserRuntime, runtime_missing
    from hermes_platform.remote_identity import capture

    tmp_path.chmod(0o700)  # this owned runtime root can inherit SGID too
    parts = [tmp_path / name for name in ("profile", "captures", "sockets", "tmp")]
    for part in parts:
        part.mkdir(mode=0o700)
        part.chmod(0o700)  # owned fixture: mkdir can inherit SGID from the runner's parent
    paths = [str(tmp_path), *map(str, parts)]
    generation = capture(lambda argv, **kwargs: subprocess.run(argv, capture_output=True, check=False, **kwargs), paths)
    runtime = BrowserRuntime(*paths, generation=generation)
    ran = subprocess.run(runtime.guarded(["printf", "ran"]), capture_output=True, text=True, timeout=30, check=False)
    assert (ran.returncode, ran.stdout) == (0, "ran")
    parts[0].rmdir()
    parts[0].symlink_to(parts[1])
    refused = subprocess.run(runtime.guarded(["printf", "ran"]), capture_output=True, text=True, timeout=30, check=False)
    assert refused.stdout == "" and runtime_missing(refused.returncode, refused.stderr)


@pytest.mark.platforms("posix")
def test_sandbox_client_preserves_nonzero_status_and_decodes_memory_output():
    import sys
    from tools import browser_tool_session as session

    code, stdout, stderr = session._run_agent_browser_client(
        [sys.executable, "-c", "import sys; print('é'); print('diagnostic',file=sys.stderr); sys.exit(3)"],
        os.environ.copy(), None, 10)
    assert (code, stdout, stderr) == (3, "é\n", "diagnostic\n")
