"""Historical updater import bridge; no service lifecycle remains.

Already-running old updater frames must finish in the new terminal-only interpreter.
"""

from hermes_cli._old_updater import stop_for_relaunch

def runtime_outside_gateway_evidence(*args, **kwargs):
    stop_for_relaunch()

def host_owes_no_gateway_restart(*args, **kwargs):
    stop_for_relaunch()
