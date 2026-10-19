"""TERMINAL_LOCAL_PERSISTENT end to end on a real local bash: on (the default) carries exported
env vars between commands, off starts every command from the login-time env. cwd carries in
both modes (file tools rely on it); plain shell vars never carry."""

import json
import shlex
import shutil
import subprocess

import pytest

from tools.terminal_tool import _get_env_config, _resolve_container_task_id, terminal_tool
from tools.terminal_tool_lifecycle import cleanup_vm


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("setting, exported", [(None, "yes"), ("true", "yes"), ("false", "none")])
def test_local_persistent_controls_exports_but_cwd_always_carries(monkeypatch, tmp_path, setting, exported):
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    if setting is None:
        monkeypatch.delenv("TERMINAL_LOCAL_PERSISTENT", raising=False)
    else:
        monkeypatch.setenv("TERMINAL_LOCAL_PERSISTENT", setting)
    task_id = f"local-persist-{setting}"
    (tmp_path / "sub").mkdir()

    def run(cmd):
        return json.loads(terminal_tool(cmd, task_id=task_id))["output"].strip()

    try:
        run("cd sub && export HERMES_LP_EXPORT=yes; HERMES_LP_PLAIN=yes")
        assert run("echo ${HERMES_LP_EXPORT:-none} ${HERMES_LP_PLAIN:-none}") == f"{exported} none"
        assert run("pwd -P") == str((tmp_path / "sub").resolve())
    finally:
        cleanup_vm(_resolve_container_task_id(task_id))


def test_local_persistent_defaults_on_and_reaches_the_environment(monkeypatch, tmp_path):
    from tools.terminal_tool_lifecycle import _create_configured_env

    monkeypatch.delenv("TERMINAL_LOCAL_PERSISTENT", raising=False)
    config = _get_env_config()
    assert config["local_persistent"] is True
    for value, task in ((config["local_persistent"], "default"), (False, "off")):
        env = _create_configured_env({**config, "local_persistent": value}, "local", image="", cwd=str(tmp_path),
                                     timeout=30, task_id=f"local-persist-config-{task}", host_cwd=None)
        try:
            assert env._persist_env is value
        finally:
            env.cleanup()


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("setting, later", [("true", "fn:no"), ("false", "fn:yes")])
def test_profile_functions_survive_only_with_persistence_off(tmp_path, setting, later):
    """The login snapshot holds profile functions; with persistence on the first command's
    ``export -p`` re-dump replaces it, so they are gone from the second command on. The docs'
    "Profile functions / aliases" row states exactly this."""
    from tools.environments.base_session_env import _snapshot_bootstrap_script, _wrap_command_script

    # This contract is Bash-specific: native sh/zsh cannot import Bash functions
    # from the wrapper. Select the actual command dialect explicitly rather than
    # falsifying the account's login shell or running Bash syntax under sh.
    bash = shutil.which("bash")
    assert bash, "Bash snapshot qualification requires Bash"
    profile = tmp_path / ".bashrc"
    profile.write_text("hermes_lp_fn(){ echo FN; }\n", encoding="utf-8")
    snapshot = tmp_path / "snapshot"
    kwargs = dict(
        quoted_cwd=shlex.quote(str(tmp_path)), quoted_snap=shlex.quote(str(snapshot)),
        snap_tmp_template=shlex.quote(str(snapshot) + ".tmp.XXXXXXXXXX"), cwd_marker="__CWD__",
    )
    bootstrap = f"source {shlex.quote(str(profile))}\n" + _snapshot_bootstrap_script(
        **kwargs, excluded_names=(),
    )
    subprocess.run([bash, "-c", bootstrap], check=True, capture_output=True, text=True, timeout=10)
    probe = "type hermes_lp_fn >/dev/null 2>&1 && echo fn:yes || echo fn:no"

    def run(cmd):
        script = _wrap_command_script(
            cmd, **kwargs, passthrough_names=(), snapshot_ready=True,
            user_shell=bash, persist_env=setting == "true",
        )
        result = subprocess.run([bash, "-c", script], check=True, capture_output=True, text=True, timeout=10)
        return result.stdout.split("\n__CWD__", 1)[0].strip()

    assert run(probe) == "fn:yes"
    assert run(probe) == later
