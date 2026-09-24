"""Live sandbox enforcement through the real tool registry (Landlock + seccomp).

Everything runs the production path: config.yaml in a temp HERMES_HOME →
``model_tools.handle_function_call`` → terminal / file tools / execute_code →
``LocalEnvironment`` / kernel spawn → launcher → kernel. Per-session changes go through
the same ``/sandbox`` dispatcher the CLI and gateway use.
"""

from __future__ import annotations

import json
import uuid

import pytest

pytestmark = pytest.mark.linux_only


@pytest.fixture
def sandbox_home(tmp_path, monkeypatch):
    from hermes_constants import get_hermes_home
    from hermes_security.sandbox.spawn import backend_status
    if not backend_status()["available"]:
        pytest.skip("kernel without Landlock")
    home, ws, outside = tmp_path / "home", tmp_path / "ws", tmp_path / "outside"
    for d in (home, ws, outside):
        d.mkdir()
    (ws / "readme.txt").write_text("workspace\n")
    (outside / "secret.txt").write_text("top-secret\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TERMINAL_CWD", str(ws))
    monkeypatch.chdir(ws)
    (get_hermes_home() / "config.yaml").write_text(json.dumps({"sandbox": {
        "enabled": True, "tools": ["terminal", "file", "code_execution"], "grants": [f"{ws}:rw"]}}))
    task = f"sbx-{uuid.uuid4().hex[:8]}"
    yield ws, outside, task
    from tools.terminal_tool import cleanup_vm
    cleanup_vm(task)


def _call(name, task, **args):
    from model_tools import handle_function_call
    return json.loads(handle_function_call(name, args, task_id=task))


def _sandbox(command):
    from hermes_cli.sandbox_command import dispatch_sandbox_command
    from hermes_security.sandbox.session import current_session_key
    return dispatch_sandbox_command(command, session_key=current_session_key()).text


def test_terminal_and_file_tools_see_only_session_grants_and_changes_apply_next_command(sandbox_home):
    ws, outside, task = sandbox_home
    secret = outside / "secret.txt"
    assert _call("terminal", task, command="cat readme.txt")["output"].strip() == "workspace"
    denied = _call("terminal", task, command=f"cat {secret}")
    assert "top-secret" not in denied["output"] and "Permission denied" in denied["output"]
    assert "error" in _call("write_file", task, path=str(outside / "pwn.txt"), content="x")
    assert not (outside / "pwn.txt").exists()

    assert "Granted" in _sandbox(f"grant {outside} --force")
    assert _call("terminal", task, command=f"cat {secret}")["output"].strip() == "top-secret"
    assert "top-secret" in _call("read_file", task, path=str(secret))["content"]

    _sandbox(f"revoke {outside}")
    assert "top-secret" not in _call("terminal", task, command=f"cat {secret}")["output"]


def test_single_file_rw_grant_is_writable_without_opening_its_directory(sandbox_home):
    ws, outside, task = sandbox_home
    rc = outside / ".zshrc"
    rc.write_text("old\n")
    _sandbox(f"grant {rc}:rw --force")
    assert _call("read_file", task, path=str(rc))["content"].endswith("old")
    assert "error" not in _call("write_file", task, path=str(rc), content="new\n")
    assert rc.read_text() == "new\n"
    assert "error" in _call("write_file", task, path=str(outside / "sibling"), content="x")


def test_unix_sockets_are_closed_until_the_session_opens_them(sandbox_home):
    import sys
    _, _, task = sandbox_home
    probe = f"{sys.executable} -I -S -c 'import socket; socket.socket(socket.AF_UNIX); print(\"SOCK\" + \"OK\")'"
    assert "SOCKOK" not in _call("terminal", task, command=probe)["output"]
    _sandbox("unix on")
    assert "SOCKOK" in _call("terminal", task, command=probe)["output"]


def test_execute_code_kernel_is_confined_and_keeps_tool_rpc(sandbox_home):
    ws, outside, task = sandbox_home
    code = (f"print(open({str(ws / 'readme.txt')!r}).read().strip())\n"
            f"try:\n    open({str(outside / 'secret.txt')!r}).read(); print('LEAK')\n"
            "except PermissionError:\n    print('denied')\n"
            "from hermes_tools import terminal\nprint(terminal('echo via-rpc')['output'])\n")
    out = _call("execute_code", task, code=code)["output"]
    assert out.split() == ["workspace", "denied", "via-rpc"]


def test_tool_allowlist_filters_schemas_and_blocks_dispatch(sandbox_home):
    from hermes_security.sandbox.tool_policy import filter_tool_definitions, tool_block_reason
    defs = [{"type": "function", "function": {"name": n}} for n in ("read_file", "terminal", "web_search")]
    assert {d["function"]["name"] for d in filter_tool_definitions(defs)} == {"read_file", "terminal"}
    assert tool_block_reason("web_search") and tool_block_reason("terminal") is None
    _sandbox("tools add web_search")
    assert tool_block_reason("web_search") is None


def test_a_grant_covering_the_hermes_home_cannot_reach_it_or_the_vault_password(sandbox_home, monkeypatch):
    """Regression (independent QA): a workspace of ``~`` made ``~/.hermes`` writable, so the
    model could move config.yaml away and the next command ran unconfined; the vault
    password was inherited by every sandboxed command."""
    from hermes_constants import get_hermes_home
    _, _, task = sandbox_home
    hermes_home = get_hermes_home()
    sibling = hermes_home.parent / f"sibling-{task}"
    sibling.write_text("reachable\n")
    monkeypatch.setenv("HERMES_MASTER_PASSWORD", "vault-pw-must-not-leak")
    _sandbox(f"grant {hermes_home.parent}:rw --force")

    out = _call("terminal", task, command=(
        f"cat {sibling}; mv {hermes_home / 'config.yaml'} {hermes_home.parent}/moved.yaml; "
        f"ls {hermes_home}; echo pw=[$HERMES_MASTER_PASSWORD]"))["output"]
    assert "reachable" in out and "pw=[]" in out
    assert (hermes_home / "config.yaml").exists()
    assert "Permission denied" in out


def test_sandbox_stays_on_when_its_config_vanishes(sandbox_home):
    from hermes_constants import get_hermes_home
    from hermes_security.sandbox.policy import sandbox_config
    assert sandbox_config()["enabled"]
    (get_hermes_home() / "config.yaml").unlink()
    assert sandbox_config()["enabled"]
