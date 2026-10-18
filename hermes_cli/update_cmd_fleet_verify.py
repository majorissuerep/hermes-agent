"""Historical updater import bridge; no service lifecycle remains.

Already-running old updater frames must finish in the new terminal-only interpreter.
"""

from hermes_cli._old_updater import stop_for_relaunch

_FLEET_PROBE_SETTLE_TIMEOUT_SECONDS = 120.0

def _print_legacy_units_warning(*args, **kwargs):
    stop_for_relaunch()

def _collect_fleet_snapshot(*args, **kwargs):
    stop_for_relaunch()

def _fleet_row_identity_pending(*args, **kwargs):
    stop_for_relaunch()

def _restarted_units_gone(*args, **kwargs):
    stop_for_relaunch()

def _live_gateway_pids_from_fleet(*args, **kwargs):
    stop_for_relaunch()

def _record_owed_gateway_inventory(*args, **kwargs):
    stop_for_relaunch()

def _named_gateways_still_owed(*args, **kwargs):
    stop_for_relaunch()

def _verify_fleet_after_update(*args, **kwargs):
    stop_for_relaunch()
