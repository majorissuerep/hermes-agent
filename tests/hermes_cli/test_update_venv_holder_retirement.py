"""PM updates do not require the currently running venv to become unoccupied."""

import os
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hermes_cli import main, update_cmd, update_cmd_holders
from tests.compat.old_updater_support import fresh_child as fresh_child, no_external_work as no_external_work  # noqa: PLC0414 -- the self-alias is load-bearing: it suppresses F811 for the pytest fixture parameter shadowing this import


@pytest.mark.real_concurrent_gate
@pytest.mark.parametrize(
    "module,name,args,kwargs",
    [
        (main, "_filter_non_gateway_concurrent_instances", ([(123, "hermes.exe")],), {}),
        (main, "_detect_concurrent_hermes_instances", (Path("Scripts"),), {"exclude_pid": 123}),
        (main, "_leftover_pausable_gateway_pids", ([(123, "python.exe", "hermes serve")],), {}),
        (main, "_ledger_manual_serve_holders", ([(123, "python.exe", "hermes serve")],), {}),
        (main, "_ledger_reapable_backend_pids", ([(123, "python.exe", "hermes serve")],), {}),
        (main, "_orphaned_desktop_backend_pids", ([(123, "python.exe", "hermes serve")],), {}),
        (main, "_handoff_reapable_backend_pids", ([(123, "python.exe", "hermes serve")],), {}),
        (main, "_stop_process_trees", ([123, (456, 789)],), {}),
    ],
)
def test_historical_holder_hooks_hand_off_without_inspecting_or_killing(
    monkeypatch, module, name, args, kwargs, fresh_child,
):
    """A historical main's holder gates hand the update to the fresh child and exit with its
    status; the old parent never classifies, inspects or kills processes itself."""
    from hermes_cli import process_identity
    import psutil

    forbidden = Mock(side_effect=AssertionError("retired holder gate performed work"))
    monkeypatch.setattr(update_cmd_holders, "_psutil", forbidden)
    monkeypatch.setattr(process_identity, "ledger_entries", forbidden)
    monkeypatch.setattr(psutil, "process_iter", forbidden)
    monkeypatch.setattr(psutil, "Process", forbidden)
    before = deepcopy((args, kwargs))
    with fresh_child.exits():
        getattr(module, name)(*args, **kwargs)
    assert (args, kwargs) == before
    forbidden.assert_not_called()


def test_relaunch_stopped_serves_is_separate_work_not_an_update(monkeypatch, fresh_child, capsys):
    """The historical atexit token restarts stopped serves through the child and returns."""
    token = {"pending": True, "entries": [{"pid": 123, "port": 9000}]}
    fresh_child.returncode = 0
    fresh_child.result = {"serves_handled": True}
    main._relaunch_stopped_serves(token)
    assert token["pending"] is False
    assert fresh_child.requests[-1]["stopped_serves"]["entries"] == token["entries"]
    assert "did not complete" not in capsys.readouterr().err


@pytest.mark.parametrize("gateway_mode", [False, True])
def test_retired_ancestor_hook_hands_off_without_any_process_access(monkeypatch, gateway_mode, fresh_child):
    """Old ancestor probes transfer to the new updater without inspecting or killing PIDs."""
    import psutil

    forbidden = Mock(side_effect=AssertionError("refusal probe performed work"))
    monkeypatch.setattr(psutil, "Process", forbidden)
    monkeypatch.setattr(os, "kill", forbidden)
    with fresh_child.exits():
        update_cmd._refuse_gateway_ancestor_tree_kill([123, 456], gateway_mode=gateway_mode)
    forbidden.assert_not_called()


def test_command_reaches_checkout_preparation_without_holder_gates(monkeypatch, tmp_path):
    from hermes_cli import update_inventory

    class ReachedCheckout(BaseException):
        pass

    reached = []
    forbidden = Mock(side_effect=AssertionError("current update called a retired holder gate"))
    monkeypatch.setattr(main, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(main, "_update_preflight_handled", lambda args: False)
    monkeypatch.setattr(main, "_install_hangup_protection", lambda **kwargs: None)
    monkeypatch.setattr(main, "_finalize_update_output", lambda token: None)
    monkeypatch.setattr(update_inventory, "collect_runtime_inventory", lambda: update_inventory.UpdatePlan())
    monkeypatch.setattr(update_cmd, "_run_pre_update_backup", lambda args: reached.append("backup"))

    # Recreate the former call-site names as tripwires, not host-OS fakes.
    # The scan remains live for lifecycle ownership, but must not gate updates.
    monkeypatch.setattr(main, "_detect_concurrent_hermes_instances", forbidden, raising=False)
    monkeypatch.setattr(update_cmd_holders, "_detect_venv_python_processes", forbidden)
    monkeypatch.setattr(update_cmd, "_refuse_gateway_ancestor_tree_kill", forbidden)
    monkeypatch.setattr(main, "_is_windows", forbidden)
    monkeypatch.setattr(os, "kill", forbidden)

    def prepare_checkout(**_):
        reached.append("checkout")
        raise ReachedCheckout

    monkeypatch.setattr(update_cmd, "_prepare_git_command", prepare_checkout)
    with pytest.raises(ReachedCheckout):
        main.cmd_update(SimpleNamespace(gateway=False, check=False, yes=True, force=False, force_venv=False))
    assert reached == ["backup", "checkout"]
    forbidden.assert_not_called()

