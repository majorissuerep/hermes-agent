"""Native startup diagnoses PM drift once without provisioning or blocking dispatch."""
import sys
import pytest


@pytest.mark.parametrize("headless", [False, True])
def test_native_startup_checks_pm_once_before_dispatch(tmp_path, monkeypatch, capsys, headless):
    import pm
    from hermes_cli import main, venv_sync, boot_bootstrap
    calls = []
    def verdict(root):
        calls.append("checked")
        return "install out of sync (fixture); run hermes pm install"
    def forbidden(*args, **kwargs):
        pytest.fail("passive startup must not provision dependencies")
    monkeypatch.setattr(venv_sync, "check_runtime", verdict)
    monkeypatch.setattr(boot_bootstrap, "maybe_run_boot_bootstrap", lambda root: None)
    monkeypatch.setattr(pm, "ensure", forbidden)
    monkeypatch.setattr(pm, "sync_venv", forbidden)
    monkeypatch.setattr(main, "_prepare_agent_startup", lambda args: None)
    monkeypatch.setattr(main, "_run_oneshot_from_args", lambda args: calls.append("headless"))
    monkeypatch.setattr(main, "_launch_tui", lambda *a, **kw: calls.append("tui"))
    monkeypatch.setattr(main, "_pin_kanban_board_env", lambda: None)
    monkeypatch.setattr(sys, "argv", ["hermes", "chat", *(["-q", "local task"] if headless else [])])
    main.main()
    assert calls == ["checked", "headless" if headless else "tui"]
    assert capsys.readouterr().err.count("install out of sync") == 1
