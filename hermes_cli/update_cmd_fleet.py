"""Historical updater import bridge; no service lifecycle remains.

Already-running old updater frames must finish in the new terminal-only interpreter.
"""

from hermes_cli._old_updater import stop_for_relaunch

_FLEET_RESTART_PENDING_NAME = '.fleet-restart-pending'

_FRESH_RESTART_SUPERVISORS = frozenset()

_SYSTEMD_SCOPES = (('user', ['systemctl', '--user']), ('system', ['systemctl']))

_LIST_GATEWAY_UNITS = ['list-units', 'hermes-gateway*', 'hermes-serve*', 'hermes-dashboard*', '--plain', '--no-legend', '--no-pager']

def _write_gateway_update_exit_code(*args, **kwargs):
    stop_for_relaunch()

def _fleet_restart_pending_marker_path(*args, **kwargs):
    stop_for_relaunch()

def _write_fleet_restart_pending_marker(*args, **kwargs):
    stop_for_relaunch()

def _current_profile_name(*args, **kwargs):
    stop_for_relaunch()

def _clear_fleet_restart_pending_marker(*args, **kwargs):
    stop_for_relaunch()

def _fleet_restart_obligation_armed(*args, **kwargs):
    stop_for_relaunch()

def _obligation_fields(*args, **kwargs):
    stop_for_relaunch()

def _current_checkout_sha(*args, **kwargs):
    stop_for_relaunch()

def _receipt_looks_unfinished(*args, **kwargs):
    stop_for_relaunch()

def _receipt_reports_stale_runtime(*args, **kwargs):
    stop_for_relaunch()

def _receipt_owed_gateways(*args, **kwargs):
    stop_for_relaunch()

def _fleet_covered_gateways(*args, **kwargs):
    stop_for_relaunch()

def _live_fleet_covers_receipt(*args, **kwargs):
    stop_for_relaunch()

_UNMAPPED_GATEWAY = ('gateway', '')

def _marker_owed_gateways(*args, **kwargs):
    stop_for_relaunch()

def _discharge_gatewayless_marker(*args, **kwargs):
    stop_for_relaunch()

def _marker_only_restart_obsolete(*args, **kwargs):
    stop_for_relaunch()

def _receipt_restart_phase_completed(*args, **kwargs):
    stop_for_relaunch()

def _pending_fleet_restart_needed(*args, **kwargs):
    stop_for_relaunch()

def _update_owes_fleet_restart(*args, **kwargs):
    stop_for_relaunch()

def _warn_pending_fleet_restart(*args, **kwargs):
    stop_for_relaunch()

def _warn_pending_fleet_restart_on_startup(*args, **kwargs):
    stop_for_relaunch()

def _systemd_gateway_unit_listings(*args, **kwargs):
    stop_for_relaunch()

def _needs_sudo(*args, **kwargs):
    stop_for_relaunch()

def _unit_main_pid(*args, **kwargs):
    stop_for_relaunch()

def _restart_systemd_gateway_units_best_effort(*args, **kwargs):
    stop_for_relaunch()

def _live_fleet_current_rows(*args, **kwargs):
    stop_for_relaunch()

def _restart_identity_sha(*args, **kwargs):
    stop_for_relaunch()

def _fleet_restart_skip_reason(*args, **kwargs):
    stop_for_relaunch()

def _run_pending_fleet_restart(*args, **kwargs):
    stop_for_relaunch()

def _systemctl(*args, **kwargs):
    stop_for_relaunch()

def _systemctl_reset_and_restart(*args, **kwargs):
    stop_for_relaunch()

def _systemd_unit_owned_by_update(*args, **kwargs):
    stop_for_relaunch()

def _scoped_manual_gateway_pids(*args, **kwargs):
    stop_for_relaunch()

def _is_hermes_gateway_unit(*args, **kwargs):
    stop_for_relaunch()

def _for_each_systemd_gateway_unit(*args, **kwargs):
    stop_for_relaunch()

def _service_unit_supports_graceful_sigusr1_restart(*args, **kwargs):
    stop_for_relaunch()

def _warn_incomplete_gateway_fleet_restart(*args, **kwargs):
    stop_for_relaunch()

def _restart_launchd_gateway_after_update(*args, **kwargs):
    stop_for_relaunch()

def _restart_macos_launchd_gateways(*args, **kwargs):
    stop_for_relaunch()

def _surviving_gateway_pids_after_failed_restart(*args, **kwargs):
    stop_for_relaunch()

_MANUAL_GATEWAY_SKIP_REASON = 'manual gateway has no supervisor relaunch authority; left running for explicit operator restart'

_DESKTOP_SERVE_SKIP_REASON = 'desktop app owns and respawns this serve backend; the recovery pass must not restart it out from under its supervisor'

_SERVE_SKIP_REASON = 'no per-profile relaunch command reaches a serve/dashboard runtime; recovered by the fresh systemd unit pass when it owns a hermes-serve* unit, else left running for explicit operator restart'

_LAUNCHD_SERVE_SKIP_REASON = 'launchd job owns this backend; the post-update dashboard cleanup kickstarts the job through launchd, never a detached argv respawn that would fight its KeepAlive'

def _gateway_recovery_partition(*args, **kwargs):
    stop_for_relaunch()

def _warn_gateway_restart_phase_aborted(*args, **kwargs):
    stop_for_relaunch()

def _drain_or_signal_gateway_for_update(*args, **kwargs):
    stop_for_relaunch()

def _gateway_home_for_pid(*args, **kwargs):
    stop_for_relaunch()

def _sudo_noninteractive_ok(*args, **kwargs):
    stop_for_relaunch()

def _resolve_manage_cmd(*args, **kwargs):
    stop_for_relaunch()

def _repair_unit_without_fatal_exit_park(*args, **kwargs):
    stop_for_relaunch()

def _restart_one_systemd_gateway_unit(*args, **kwargs):
    stop_for_relaunch()

def _restart_systemd_gateway_units(*args, **kwargs):
    stop_for_relaunch()

class _GatewayRestartOutcome:
    def __init__(self, *args, **kwargs):
        stop_for_relaunch()

def _restart_manual_gateways(*args, **kwargs):
    stop_for_relaunch()

def _force_kill_stuck_gateways(*args, **kwargs):
    stop_for_relaunch()

def _recover_after_restart_phase_abort(*args, **kwargs):
    stop_for_relaunch()

def _gateway_drain_budget(*args, **kwargs):
    stop_for_relaunch()

def _restart_gateway_fleet_after_update(*args, **kwargs):
    stop_for_relaunch()

def _restart_phase_failure_is_incomplete(*args, **kwargs):
    stop_for_relaunch()

def _fleet_probe_expected_runtimes(*args, **kwargs):
    stop_for_relaunch()

def _wait_for_service_active(*args, **kwargs):
    stop_for_relaunch()
