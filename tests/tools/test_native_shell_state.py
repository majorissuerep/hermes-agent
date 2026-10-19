"""Native POSIX command status and state survive the bash snapshot boundary."""

import shlex
import shutil
import subprocess

import pytest

from tools.environments.base_session_env import _split_cwd_marker, _wrap_command_script


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("command_status", [7, 130])
def test_native_shell_preserves_status_directory_and_exports(tmp_path, command_status):
    shell = shutil.which("dash") or shutil.which("sh")
    bash = shutil.which("bash")
    assert shell and bash, "native terminal qualification requires POSIX sh and bash"
    nested = tmp_path / "nested ' directory"
    nested.mkdir()
    snapshot = tmp_path / "session ' snapshot"
    snapshot.write_text("")
    marker = "__NATIVE_STATE__"
    script = _wrap_command_script(
        f"cd {shlex.quote(str(nested))}; export NATIVE_STATE_ORACLE=retained; exit {command_status}",
        quoted_cwd=shlex.quote(str(tmp_path)), quoted_snap=shlex.quote(str(snapshot)),
        snap_tmp_template=shlex.quote(str(snapshot) + ".tmp.XXXXXXXXXX"),
        passthrough_names=(), snapshot_ready=True, cwd_marker=marker, user_shell=shell,
    )
    result = subprocess.run([bash, "-c", script], text=True, capture_output=True, timeout=10, check=False)
    assert result.returncode == command_status, result.stderr
    assert _split_cwd_marker(result.stdout, marker)[0] == str(nested)
    resumed = subprocess.run(
        [bash, "-c", f"source {shlex.quote(str(snapshot))}; printf '%s' \"$NATIVE_STATE_ORACLE\""],
        text=True, capture_output=True, timeout=10, check=False,
    )
    assert resumed.returncode == 0, resumed.stderr
    assert resumed.stdout == "retained"
    assert snapshot.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.userenv*"))
