"""Fork regression: a foreign Hermes checkout on PYTHONPATH must not shadow the
running checkout's packages.

Incident (manual QA emulation, 2026-10-22): a shell environment carrying
``PYTHONPATH=<installed ~/.hermes/hermes-agent>`` (an upstream/pre-fork tree
without ``hermes_security``) made every fork import resolve from the FOREIGN
tree — sealed config.yaml was then read as raw envelope bytes and every surface
reported "formatting error / utf-8 codec can't decode". ``harden_import_path``
must drop structurally-foreign Hermes roots (directories shipping
``hermes_bootstrap.py``) so the checkout that owns the process also owns its
state code.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _foreign_tree(tmp_path: Path) -> Path:
    """A structurally-real foreign checkout: bootstrap + the colliding packages."""
    foreign = tmp_path / "foreign-hermes"
    (foreign / "hermes_cli").mkdir(parents=True)
    (foreign / "hermes_cli" / "__init__.py").write_text("", encoding="utf-8")
    (foreign / "utils.py").write_text("raise AssertionError('foreign utils executed')\n", encoding="utf-8")
    (foreign / "hermes_bootstrap.py").write_text(
        "raise AssertionError('foreign bootstrap executed')\n", encoding="utf-8")
    return foreign


def _own_first_env(foreign: Path, tmp_path: Path) -> dict:
    """The fork's child-env shape: own root FIRST on PYTHONPATH, foreign after."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("HERMES_PYTHON_SRC_ROOT", "PYTHONPATH")}
    env["PYTHONPATH"] = os.pathsep.join([str(PROJECT_ROOT), str(foreign)])
    env["HERMES_HOME"] = str(tmp_path / "home")
    return env


def test_own_launch_shape_never_runs_foreign_bootstrap(tmp_path):
    """Own root FIRST (the fork's child-env shape): the own bootstrap wins the
    import and the guard drops the foreign entry from sys.path."""
    foreign = _foreign_tree(tmp_path)
    code = (
        "import hermes_bootstrap, sys, os; "
        f"assert sys.path[0] == {str(PROJECT_ROOT)!r}, sys.path; "
        f"assert {str(foreign)!r} not in [os.path.abspath(p) for p in sys.path], sys.path; "
        "import utils; assert 'foreign-hermes' not in utils.__file__"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        env=_own_first_env(foreign, tmp_path), cwd=tmp_path, timeout=120,
        stdin=subprocess.DEVNULL, check=False,
    )
    assert result.returncode == 0, result.stderr[-1500:]


def test_foreign_root_ahead_of_own_is_dropped(tmp_path):
    """The dangerous position: foreign AHEAD of the own root. The import race
    for ``hermes_bootstrap`` itself is unwinnable from inside the losing
    module — the deliverable contract is that the OWN bootstrap, however it is
    reached, evicts every foreign Hermes root (ahead or behind) from sys.path.
    (Launchers that pop PYTHONPATH, and the fork's child envs putting the own
    root first, are what get the own bootstrap reached in production.)"""
    foreign = _foreign_tree(tmp_path)
    code = (
        f"import sys, os; sys.path[:] = [os.fspath({str(foreign)!r}), os.fspath({str(PROJECT_ROOT)!r}), *sys.path]; "
        # Load the OWN bootstrap by file location: the foreign tree must not
        # execute, and its entries must not survive the hardening.
        f"import importlib.util; spec = importlib.util.spec_from_file_location("
        f"'own_hermes_bootstrap', {str(PROJECT_ROOT / 'hermes_bootstrap.py')!r}); "
        "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); "
        "mod.harden_import_path(); "
        f"assert sys.path[0] == {str(PROJECT_ROOT)!r}, sys.path; "
        f"assert {str(foreign)!r} not in [os.path.abspath(p) for p in sys.path], sys.path"
    )
    env = {k: v for k, v in os.environ.items() if k != "HERMES_PYTHON_SRC_ROOT"}
    env["HERMES_HOME"] = str(tmp_path / "home")
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        env=env, cwd=tmp_path, timeout=120, stdin=subprocess.DEVNULL, check=False,
    )
    assert result.returncode == 0, result.stderr[-1500:]
