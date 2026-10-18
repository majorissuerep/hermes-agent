"""Takeover refuses exact active owners without signalling processes or managing services."""
from contextlib import contextmanager
import os
import shlex
from pathlib import Path
import subprocess
import sys

import pytest
from tests.waiting import wait_until

ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def _child(tmp_path, home, *, lookalike=False, holds_database=False):
    package = tmp_path / "gateway"
    package.mkdir(exist_ok=True)
    (package / "__init__.py").touch()
    ready = tmp_path / "ready"
    home.mkdir(parents=True, exist_ok=True)
    setup = f"import sqlite3; db = sqlite3.connect({str(home / 'state.db')!r}); " if holds_database else ""
    source = setup + f"import sys; from pathlib import Path; Path({str(ready)!r}).touch(); sys.stdin.read()"
    (package / "run.py").write_text(source)
    env = dict(os.environ, HOME=str(tmp_path), HERMES_HOME=str(home), PYTHONPATH=str(tmp_path))
    command = ([sys.executable, "-c", source, "gateway run", "hermes-agent --yolo"] if lookalike
               else [sys.executable, "-m", "gateway.run"])
    child = subprocess.Popen(command, cwd=tmp_path, env=env, stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        wait_until(ready.exists, timeout=5, what="isolated test child ready")
        yield child
    finally:
        child.stdin.close()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
        child.stderr.close()


def _preflight(tmp_path, home):
    forbidden = tmp_path / "forbidden-service-action"
    executables = tmp_path / "bin"
    executables.mkdir(exist_ok=True)
    for name in ("systemctl", "pkill", "pgrep"):
        stub = executables / name
        stub.write_text("#!/bin/sh\ntouch " + shlex.quote(str(forbidden)) + "\nexit 1\n")
        stub.chmod(0o700)
    env = dict(os.environ, HOME=str(tmp_path), HERMES_HOME=str(home),
               PATH=str(executables) + os.pathsep + os.environ["PATH"])
    result = subprocess.run(["bash", "-c", 'source "$1"; require_quiescent_home',
                           "takeover-probe", str(ROOT / "scripts/takeover.sh")],
                          env=env, capture_output=True, text=True, timeout=15, check=False)
    assert not forbidden.exists(), "takeover must not invoke service managers or broad process matching"
    return result


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("foreign,lookalike,refused", [(False, False, True), (True, False, False),
                                                       (False, True, False)])
def test_takeover_refuses_only_owned_runtime_and_never_kills_it(tmp_path, foreign, lookalike, refused):
    home = tmp_path / "state"
    active_home = tmp_path / "other" if foreign else home
    with _child(tmp_path, active_home, lookalike=lookalike) as child:
        result = _preflight(tmp_path, home)
        assert result.returncode == int(refused), result.stdout + result.stderr
        assert child.poll() is None
        if refused:
            assert str(child.pid) in result.stderr and "Close Hermes sessions/tasks" in result.stderr


@pytest.mark.platforms("posix")
def test_takeover_refuses_native_maintenance_lock_without_signalling(tmp_path):
    from hermes_state.runtime_ownership import ProfileOwnership
    home = tmp_path / "state"
    owner = ProfileOwnership()
    owner.reserve([home])
    try:
        result = _preflight(tmp_path, home)
        assert result.returncode == 1 and "Takeover refused" in result.stderr
    finally:
        owner.close()
    assert _preflight(tmp_path, home).returncode == 0


@pytest.mark.platforms("linux")
def test_takeover_refuses_real_database_holder_even_with_an_inline_launcher(tmp_path):
    home = tmp_path / "state"
    with _child(tmp_path, home, lookalike=True, holds_database=True) as child:
        result = _preflight(tmp_path, home)
        assert result.returncode == 1 and str(child.pid) in result.stderr
        assert child.poll() is None
