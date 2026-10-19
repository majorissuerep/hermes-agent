"""Native launcher/runtime startup responsibilities are not duplicated."""
import sys
import types
from argparse import Namespace

import pytest


def _args(command):
    return Namespace(accept_hooks=True, command=command, cron_command="run" if command == "cron" else None,
                     mcp_action=None, tui=False, yolo=False, safe_mode=False, ignore_user_config=False)


@pytest.mark.parametrize("command", [None, "chat"])
def test_headless_chat_registers_local_hooks(monkeypatch, command):
    from hermes_cli import main as main_mod
    calls = []
    monkeypatch.setattr(main_mod, "_resolve_use_tui", lambda _a: False)
    monkeypatch.setattr(main_mod, "_bypass_chat_launch", lambda _a: False, raising=False)
    monkeypatch.setattr(main_mod, "_guard_noninteractive_user_config", lambda _a: None)
    monkeypatch.setattr("hermes_cli.mcp_startup.start_background_mcp_discovery", lambda **_k: None)
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", types.SimpleNamespace(
        discover_plugins=lambda: None, start_background_plugin_discovery=lambda: None))
    monkeypatch.setitem(sys.modules, "agent.shell_hooks", types.SimpleNamespace(
        register_from_config=lambda *a, **k: calls.append("hooks")))
    monkeypatch.setitem(sys.modules, "agent.outbound_webhooks", types.SimpleNamespace(
        register_from_config=lambda *a, **k: calls.append("webhooks")))
    main_mod._prepare_agent_startup(_args(command))
    assert calls == ["hooks", "webhooks"]


def test_local_scheduled_command_registers_hooks(monkeypatch):
    from hermes_cli import main as main_mod
    calls = []
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", types.SimpleNamespace(
        discover_plugins=lambda: None, start_background_plugin_discovery=lambda: None))
    monkeypatch.setitem(sys.modules, "hermes_cli.config", types.SimpleNamespace(
        load_config=dict, read_raw_config=dict))
    monkeypatch.setitem(sys.modules, "agent.shell_hooks", types.SimpleNamespace(
        register_from_config=lambda *a, **k: calls.append("hooks")))
    monkeypatch.setitem(sys.modules, "agent.outbound_webhooks", types.SimpleNamespace(
        register_from_config=lambda *a, **k: calls.append("webhooks")))
    from hermes_cli import mcp_startup
    monkeypatch.setattr(mcp_startup, "start_background_mcp_discovery", lambda **k: None)
    main_mod._prepare_agent_startup(_args("cron"))
    assert calls == ["hooks", "webhooks"]


def test_deferred_startup_spawns_no_mcp_and_registers_no_hooks(monkeypatch):
    import cli as cli_mod
    calls = []
    monkeypatch.setenv("HERMES_DEFER_AGENT_STARTUP", "1")
    monkeypatch.setattr(cli_mod, "_deferred_agent_startup_done", False)
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", types.SimpleNamespace(
        discover_plugins=lambda: calls.append("plugins")))
    from hermes_cli import mcp_startup
    monkeypatch.setattr(mcp_startup, "start_background_mcp_discovery", lambda **k: calls.append("mcp"))
    monkeypatch.setitem(sys.modules, "agent.shell_hooks", types.SimpleNamespace(
        register_from_config=lambda *a, **k: calls.append("hooks")))
    monkeypatch.setitem(sys.modules, "agent.outbound_webhooks", types.SimpleNamespace(
        register_from_config=lambda *a, **k: calls.append("webhooks")))
    cli_mod._prepare_deferred_agent_startup()
    assert calls == ["plugins"]




def test_session_store_open_runs_no_state_db_or_checkpoint_maintenance(monkeypatch):
    import cli as cli_mod
    from cli import HermesCLI
    calls = []
    monkeypatch.setattr(cli_mod, "_run_state_db_auto_maintenance", lambda db: calls.append("state.db"))
    monkeypatch.setattr(cli_mod, "_run_checkpoint_auto_maintenance", lambda: calls.append("checkpoints"))
    import tools.checkpoint_maintenance as ckpt
    monkeypatch.setattr(ckpt, "auto_prune_from_config", lambda *a, **k: calls.append("checkpoints"))
    cli = types.SimpleNamespace()
    HermesCLI._init_session_store(cli)
    assert calls == []
