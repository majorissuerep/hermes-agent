"""A quiet worker must not leave the classic terminal visually frozen."""

from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("agent_running, expected", [(True, [0.1]), (False, [])])
def test_repaint_heartbeat_follows_active_turn_without_repainting_idle_prompt(monkeypatch, agent_running, expected):
    from hermes_cli.cli_tui_mixin import CLITuiMixin

    paints = []
    cli = SimpleNamespace(
        _should_exit=False, _app=object(), _command_running=False,
        _agent_running=agent_running, _invalidate=lambda *, min_interval: paints.append(min_interval),
    )
    # One scheduler tick, without wall-clock timing or model callbacks. The worker can be
    # silently busy for minutes: it is the repaint heartbeat's job to advance its live chrome.
    monkeypatch.setattr("hermes_cli.cli_tui_mixin.time.sleep", lambda _: setattr(cli, "_should_exit", True))
    CLITuiMixin._tui_spinner_loop(cli)
    assert paints == expected
