"""Historical updater import bridge; no service lifecycle remains.

Already-running old updater frames must finish in the new terminal-only interpreter.
"""

from hermes_cli._old_updater import stop_for_relaunch

def _serve_unit_recovery_available(*args, **kwargs):
    stop_for_relaunch()

def _surviving_pre_update_serve_runtimes(*args, **kwargs):
    stop_for_relaunch()

def _numeric(*args, **kwargs):
    stop_for_relaunch()

def _qualified_serve_skips(*args, **kwargs):
    stop_for_relaunch()

def _run_fresh_recovery_process(*args, **kwargs):
    stop_for_relaunch()

def _parse_serve_units(*args, **kwargs):
    stop_for_relaunch()

def _recover_gateway_restart_after_abort(*args, **kwargs):
    stop_for_relaunch()

def _warn_stale_serve_runtimes(*args, **kwargs):
    stop_for_relaunch()

def _owed_stale_serve_rows(*args, **kwargs):
    stop_for_relaunch()

def _abort_recovery_is_complete(*args, **kwargs):
    stop_for_relaunch()
