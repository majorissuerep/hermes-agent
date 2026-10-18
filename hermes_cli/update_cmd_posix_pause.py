"""Historical updater import bridge; no service lifecycle remains.

Already-running old updater frames must finish in the new terminal-only interpreter.
"""

from hermes_cli._old_updater import stop_for_relaunch

_STOP_TIMEOUT_S = 120.0

_READY_TIMEOUT_S = 30.0

class PauseRefused:
    def __init__(self, *args, **kwargs):
        stop_for_relaunch()

def _empty_token(*args, **kwargs):
    stop_for_relaunch()

def arm_pause(*args, **kwargs):
    stop_for_relaunch()

def gateways_paused(*args, **kwargs):
    stop_for_relaunch()

def _unit_cgroup(*args, **kwargs):
    stop_for_relaunch()

def _pid_cgroup(*args, **kwargs):
    stop_for_relaunch()

def _inside(*args, **kwargs):
    stop_for_relaunch()

def _discover_systemd(*args, **kwargs):
    stop_for_relaunch()

def _discover_launchd(*args, **kwargs):
    stop_for_relaunch()

def _home_of(*args, **kwargs):
    stop_for_relaunch()

def _supervisor_markers(*args, **kwargs):
    stop_for_relaunch()

def _respawning_service(*args, **kwargs):
    stop_for_relaunch()

def _discover_bare(*args, **kwargs):
    stop_for_relaunch()

def _ppid(*args, **kwargs):
    stop_for_relaunch()

def _escape_cgroup(*args, **kwargs):
    stop_for_relaunch()

def _left_running(*args, **kwargs):
    stop_for_relaunch()

def _manage_cmd(*args, **kwargs):
    stop_for_relaunch()

def _stop_unit(*args, **kwargs):
    stop_for_relaunch()

def _start_unit(*args, **kwargs):
    stop_for_relaunch()

def _stop_job(*args, **kwargs):
    stop_for_relaunch()

def _start_job(*args, **kwargs):
    stop_for_relaunch()

def _alive(*args, **kwargs):
    stop_for_relaunch()

def _wait_gone(*args, **kwargs):
    stop_for_relaunch()

def _ask_to_drain(*args, **kwargs):
    stop_for_relaunch()

def _stop_at_once(*args, **kwargs):
    stop_for_relaunch()

def _drain_budget(*args, **kwargs):
    stop_for_relaunch()

def _stop_gateways(*args, **kwargs):
    stop_for_relaunch()

def _disarm(*args, **kwargs):
    stop_for_relaunch()

def _names(*args, **kwargs):
    stop_for_relaunch()

def _gateway_on_home(*args, **kwargs):
    stop_for_relaunch()

def _replay_env(*args, **kwargs):
    stop_for_relaunch()

def _relaunch_bare(*args, **kwargs):
    stop_for_relaunch()

def _describe(*args, **kwargs):
    stop_for_relaunch()

def pause_at_commit_point(*args, **kwargs):
    stop_for_relaunch()

def _pause(*args, **kwargs):
    stop_for_relaunch()

def resume_paused_set(*args, **kwargs):
    stop_for_relaunch()

def already_restarted(*args, **kwargs):
    stop_for_relaunch()
