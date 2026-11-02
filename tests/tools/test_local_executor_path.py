"""Native login shells retain executor locations from the spawning environment."""
import os

import pytest

from tests.tools.process_receipt_fixtures import encrypted_process_home as _encrypted_process_home

pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]


@pytest.mark.parametrize("relative", [False, True])
@pytest.mark.parametrize("background", [False, True])
def test_login_reset_keeps_absolute_and_project_relative_tools(tmp_path, monkeypatch, relative, background):
    from hermes_cli.config import save_config
    from tools.environments.local import LocalEnvironment
    from tools.process_registry import ProcessRegistry

    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    directory = project / "bin"
    directory.mkdir(parents=True)
    executable = directory / "fixture-coding-agent"
    executable.write_text("#!/bin/sh\nprintf 'EXECUTOR-PATH-OK\\n'\n", encoding="utf-8")
    executable.chmod(0o700)
    for name in (".bash_profile", ".bashrc"):
        (tmp_path / name).write_text("export PATH=/usr/bin:/bin\n", encoding="utf-8")
    save_config({"terminal": {"shell_init_files": [str(tmp_path / ".bashrc")]}}, strip_defaults=False)
    env = {"PATH": ("bin" if relative else str(directory)) + os.pathsep + "/usr/bin:/bin"}
    registry, terminal = ProcessRegistry(), None
    try:
        if background:
            session = registry.spawn_local("fixture-coding-agent", cwd=str(project), env_vars=env, restore_login_path=True)
            result = registry.wait(session.id, timeout=20)
            assert result["exit_code"] == 0 and "EXECUTOR-PATH-OK" in result["output"], result
        else:
            terminal = LocalEnvironment(cwd=str(project), env=env, timeout=20)
            result = terminal.execute("fixture-coding-agent", timeout=20)
            assert result["returncode"] == 0 and result["output"].strip() == "EXECUTOR-PATH-OK", result
    finally:
        if terminal is not None:
            terminal.cleanup()
        registry.kill_all(source="test")
