"""Frozen names imported by an already-running historical source updater."""
REAPABLE_PURPOSES = frozenset({"serve", "dashboard", "gateway", "mcp-helper"})


def ledger_entries(*args, **kwargs):
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()


def spawner_is_dead(*args, **kwargs):
    from hermes_cli._old_updater import stop_for_relaunch
    stop_for_relaunch()
