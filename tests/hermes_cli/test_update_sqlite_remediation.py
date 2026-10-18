"""Selected-runtime safety and historical updater completion correlation."""
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from hermes_cli import update_cmd, update_cmd_maint


@pytest.mark.parametrize('verdict', ['safe', 'unsafe', 'unavailable'])
@pytest.mark.parametrize('message', ['✓ Update complete!', '✓ Already up to date!'])
@pytest.mark.parametrize('action_id', ['a' * 32, 'invalid'])
def test_selected_sqlite_controls_completion_and_action_receipt(tmp_path, monkeypatch, capsys, verdict, message, action_id):
    selected = tmp_path / 'selected/python'
    info = None if verdict == 'unavailable' else SimpleNamespace(
        wal_reset_vulnerable=verdict == 'unsafe', sqlite_version_string='3.46.1')
    probes = []
    monkeypatch.setattr(sys, 'executable', str(selected))
    monkeypatch.setattr('hermes_constants.project_venv_dir', lambda _: tmp_path / 'obsolete-venv')
    monkeypatch.setattr('hermes_cli.sqlite_runtime.probe_sqlite_runtime', lambda python: probes.append(python) or info)
    monkeypatch.setattr(update_cmd, '_branch_head_suffix', lambda: '')
    monkeypatch.setenv('HERMES_ACTION_ID', action_id)
    assert update_cmd_maint._print_verified_update_completion(message) is (verdict != 'unsafe')
    assert probes == [Path(selected)]
    output = capsys.readouterr().out
    assert (message in output) is (verdict != 'unsafe')
    assert ('=== hermes-update completed' in output) is (verdict != 'unsafe' and action_id != 'invalid')
    if verdict == 'unsafe':
        for text in ('SQLite (3.46.1)', 'corruption bug', 'run the installer again', 'hermes doctor'):
            assert text in output
    elif action_id != 'invalid':
        assert f'=== hermes-update completed {action_id} ===' in output
