"""The renderer's store-Python child must activate dependencies before sealed-key adoption."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from tests.e2e.vault_home import seal_fixture_home, spawn_unlocked

pytestmark = [pytest.mark.platforms('linux', 'macos'), pytest.mark.live_system_guard_bypass]


def test_store_python_adopts_key_before_private_runtime_start(tmp_path, monkeypatch):
    import tui_runtime

    source = Path(tui_runtime.__file__).resolve().parents[1]
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'config.yaml').write_text('updates:\n  check: false\n', encoding='utf-8')
    seal_fixture_home(home)
    # A borrowed data root needs its own explicit PM selection. Test interpreters
    # carry dependencies already; store Python correctly refuses an unprepared home.
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from pm import sync_venv

    monkeypatch.setenv('HERMES_HOME', str(home))
    token = set_hermes_home_override(home)
    try:
        sync_venv(extras=['all'], explicit=True, project_root=source)
    finally:
        reset_hermes_home_override(token)
    env = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'WINDIR') if key in os.environ}
    env.update(HOME=str(tmp_path), HERMES_HOME=str(home), PYTHONPATH=str(source))
    # This is the real venv base interpreter, not its dependency-bearing test interpreter.
    # Node launches this store Python with the source path and the one-shot key descriptor.
    python = str(Path(sys._base_executable).resolve())
    assert Path(python) != Path(sys.executable).absolute()
    child = spawn_unlocked([python, '-m', 'tui_runtime'], cwd=tmp_path, env=env,
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, start_new_session=True)
    try:
        stdout, stderr = child.communicate('', timeout=40)
    except BaseException:
        os.killpg(child.pid, signal.SIGKILL)
        child.communicate(timeout=10)
        raise
    assert child.returncode == 0, stderr
    events = [json.loads(line) for line in stdout.splitlines() if line.startswith('{')]
    assert any(event.get('params', {}).get('type') == 'runtime.ready' for event in events), (stdout, stderr)
