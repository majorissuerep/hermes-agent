"""A serialized pre-terminal-only update hands off instead of restarting retired services."""
import json
from pathlib import Path
import pytest


def test_old_frame_reaches_one_takeover_and_discharges_resume(monkeypatch):
    from hermes_cli import _old_updater
    from hermes_cli import update_cmd_fleet
    payload = json.loads((Path(__file__).parents[1] / 'compat/terminal_old_update_payload.json').read_text())
    calls = []
    token = payload['windows_gateway_resume']
    def child(request):
        calls.append(request)
        return 0, {'resume_handled': True, 'receipt_handled': False}
    monkeypatch.setattr(_old_updater, '_result', None)
    monkeypatch.setattr(_old_updater, '_run_child', child)
    namespace = {'__name__': 'hermes_cli.main', 'payload': payload, 'bridge': update_cmd_fleet._restart_gateway_fleet_after_update}
    # This is the old frame's serialized call shape, not a source-shape assertion.
    exec('def cmd_update():\n'
         '    _windows_gateway_resume = payload["windows_gateway_resume"]\n'
         '    gateway_mode = payload["gateway_mode"]\n'
         '    had_desktop_app_before_update = payload["had_desktop_app_before_update"]\n'
         '    pre_update_snapshot_id = payload["pre_update_snapshot_id"]\n'
         '    pre_update_version = payload["pre_update_version"]\n'
         '    _pre_update_plan = payload["plan"]\n'
         '    return bridge(_pre_update_plan, gateway_mode)\n', namespace)
    with pytest.raises(SystemExit) as result:
        namespace['cmd_update']()
    assert result.value.code == 0
    assert len(calls) == 1
    assert calls[0]['pre_update_snapshot_id'] == payload['pre_update_snapshot_id']
    assert calls[0]['plan'] == payload['plan']
    assert token['resume_needed'] is False
