"""Fresh publication must not leave Git maintenance operating on the old path."""
import json
import os
from pathlib import Path
import shlex
import subprocess

import pytest

pytestmark = pytest.mark.platforms("posix")
INSTALLER = Path(__file__).resolve().parents[3] / "scripts" / "install.sh"


def _git(path, *args, env=None):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True, env=env, timeout=60,
    ).stdout.strip()


@pytest.mark.parametrize("pinned", [False, True], ids=["branch-tip", "pinned"])
def test_filtered_clone_publication_keeps_maintenance_in_custody(tmp_path, pinned):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.name", "Installer fixture")
    _git(origin, "config", "user.email", "installer@example.invalid")
    _git(origin, "config", "uploadpack.allowFilter", "true")
    for index in range(4):
        (origin / "payload").write_bytes(bytes([index]) * 65536)
        _git(origin, "add", "payload")
        _git(origin, "commit", "-qm", f"fixture {index}")
    expected = _git(origin, "rev-parse", "HEAD~1" if pinned else "HEAD")
    config = tmp_path / "gitconfig"
    config.write_text(
        "[maintenance]\n\tauto = true\n[gc]\n\tauto = 1\n"
        "\tautoPackLimit = 1\n\tautoDetach = true\n"
    )
    trace = tmp_path / "trace.jsonl"
    install = tmp_path / "installed"
    env = dict(os.environ, HOME=str(tmp_path), GIT_CONFIG_GLOBAL=str(config),
               GIT_CONFIG_NOSYSTEM="1", GIT_TRACE2_EVENT=str(trace),
               HERMES_HOME=str(tmp_path / "home"), HERMES_INSTALL_DIR=str(install),
               HERMES_REPO_URL=origin.as_uri())
    body = f"source {shlex.quote(str(INSTALLER))} --manifest\n"
    body += f"INSTALL_COMMIT={shlex.quote(expected if pinned else '')}\nstage_repository\n"
    result = subprocess.run(["bash", "-c", body], env=env, capture_output=True,
                            text=True, timeout=60, check=False)
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    detached = [event for event in events if event.get("event") == "child_start"
                and "maintenance" in event.get("argv", [])
                and "--detach" in event.get("argv", [])]
    assert result.returncode == 0, (result.stdout, result.stderr, detached)
    assert not detached, detached
    assert not list(tmp_path.glob(".hermes-clone-*"))
    assert _git(install, "rev-parse", "HEAD") == expected
    assert (install / "payload").read_bytes() == bytes([2 if pinned else 3]) * 65536
    _git(install, "fsck", "--connectivity-only")
    assert _git(install, "config", "--get", "maintenance.auto", env=env) == "true"
    assert _git(install, "config", "--get", "gc.autoDetach", env=env) == "true"
