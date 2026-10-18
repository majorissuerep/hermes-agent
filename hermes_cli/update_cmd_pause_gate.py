"""Historical updater import bridge; no service lifecycle remains.

Already-running old updater frames must finish in the new terminal-only interpreter.
"""

from hermes_cli._old_updater import stop_for_relaunch

def _checkout_move(*args, **kwargs):
    stop_for_relaunch()

def _moves_for(*args, **kwargs):
    stop_for_relaunch()

def _settle_checkout_move(*args, **kwargs):
    stop_for_relaunch()
