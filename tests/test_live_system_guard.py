"""Regression tests for the conftest live-system guard's argv handling.

The guard must treat only argv[0] of a list/tuple command as the executable
(arguments are data: a file named ``skill`` is not the ``skill`` binary),
while still scanning every token of wrapper invocations like ``bash -c``.
All blocked-case commands use patterns that match no real process, so a
guard regression cannot kill anything.
"""

import os
import subprocess
import sys

import pytest


def test_argv_arguments_are_not_treated_as_executables(tmp_path):
    """A file argument whose basename is a killer name must not trip the
    guard (the path contains "hermes" via the pytest tmp root)."""
    target = tmp_path / "skill"
    target.write_text("just a filename\n")
    result = subprocess.run(["cat", str(target)], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "just a filename" in result.stdout


def test_direct_killer_argv_is_still_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["pkill", "-f", "hermes-guard-regression-nomatch"], check=False)


def test_wrapped_killer_command_is_still_blocked():
    """argv[0]-only scanning must not exempt commands hidden behind a
    shell wrapper."""
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["bash", "-c", "pkill -f hermes-guard-regression-nomatch"], check=False)


def test_env_wrapped_killer_command_is_still_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["env", "GUARD_TEST=1", "pkill", "-f", "hermes-guard-regression-nomatch"], check=False)






def test_custom_home_tmpdir_is_relocated_before_pytest_uses_it(tmp_path):
    home = tmp_path / "custom-home"
    scratch = home / "cache" / "scratch"
    scratch.mkdir(parents=True)
    env = dict(os.environ, HERMES_HOME=str(home), TMPDIR=str(scratch))
    env.pop("HERMES_SCRATCH_DIR", None)
    result = subprocess.run(
        [sys.executable, "-c", "import tempfile, tests.conftest; print(tempfile.gettempdir())"],
        env=env, capture_output=True, text=True, check=True,
    )
    assert not result.stdout.strip().startswith(str(home))


def test_default_home_unmarked_tmpdir_is_relocated_before_pytest_uses_it(tmp_path):
    # Model the operator's default root with a disposable HOME, not ~/.hermes.
    operator_home = tmp_path / "operator"
    scratch = operator_home / ".hermes" / "cache" / "scratch"
    scratch.mkdir(parents=True)
    env = dict(os.environ, HOME=str(operator_home), TMPDIR=str(scratch))
    env.pop("HERMES_HOME", None)
    env.pop("HERMES_SCRATCH_DIR", None)
    result = subprocess.run(
        [sys.executable, "-c", "import tempfile, tests.conftest; print(tempfile.gettempdir())"],
        env=env, capture_output=True, text=True, check=True,
    )
    assert not result.stdout.strip().startswith(str(operator_home / ".hermes"))
