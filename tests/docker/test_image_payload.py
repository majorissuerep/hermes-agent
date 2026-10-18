"""The image's non-root runtime uses its staged tools and generated assets."""
from __future__ import annotations

import subprocess


def test_python_uses_pm_interpreter_as_runtime_user(built_image: str) -> None:
    probe = """
from pathlib import Path
import sys
from pm.lock import Facts
from pm.registry import get_package
from pm.store import current_target

store = Path('/opt/hermes/tools')
assert not list(store.glob('fetch-*')), 'completed download archives must not ship'
fact = Facts(store / 'facts.json').get('python')
expected = get_package('python').binary(store / fact['entry'], current_target())
assert Path(sys._base_executable).resolve() == expected.resolve()
import hermes_yaml as yaml
print('PM interpreter and application dependencies load as hermes')
"""
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--user", "hermes",
         "--entrypoint", "/opt/hermes/.venv/bin/python", built_image, "-c", probe],
        capture_output=True, text=True, timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_terminal_ships_compiled_bundle_without_build_environment(built_image: str) -> None:
    probe = """
from pathlib import Path
assert Path('/opt/hermes/ui-tui/dist/entry.js').is_file()
assert not Path('/opt/hermes/node_modules/vite').exists()
assert not Path('/opt/hermes/node_modules/esbuild').exists()
assert Path('/opt/hermes/node_modules/typescript/bin/tsc').is_file()
"""
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--user", "hermes",
         "--entrypoint", "/opt/hermes/.venv/bin/python", built_image, "-c", probe],
        capture_output=True, text=True, timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
