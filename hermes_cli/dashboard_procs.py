"""Historical updater import bridge; no service lifecycle remains.

Already-running old updater frames must finish in the new terminal-only interpreter.
"""

from hermes_cli._old_updater import stop_for_relaunch

def _empty_result(*args, **kwargs):
    stop_for_relaunch()

def _append_row(*args, **kwargs):
    stop_for_relaunch()

def _iter_process_table(*args, **kwargs):
    stop_for_relaunch()

def _scan_dashboard_processes(*args, **kwargs):
    stop_for_relaunch()

def _ledger_serve_binds(*args, **kwargs):
    stop_for_relaunch()

def _pid_environ(*args, **kwargs):
    stop_for_relaunch()

def _pid_passwd_home(*args, **kwargs):
    stop_for_relaunch()

def _hermes_home_for_pid(*args, **kwargs):
    stop_for_relaunch()

def _dashboard_subcommand_index(*args, **kwargs):
    stop_for_relaunch()

def _profile_flag_value(*args, **kwargs):
    stop_for_relaunch()

def _is_ephemeral_port_zero_backend(*args, **kwargs):
    stop_for_relaunch()

def _normalize_dashboard_cmdline(*args, **kwargs):
    stop_for_relaunch()

def _resolved_home(*args, **kwargs):
    stop_for_relaunch()

def _normalized_home_for_compare(*args, **kwargs):
    stop_for_relaunch()

def _pids_owned_by_hermes_home(*args, **kwargs):
    stop_for_relaunch()

def _profile_key_for_respawn(*args, **kwargs):
    stop_for_relaunch()

def _filter_dashboard_respawn_candidates(*args, **kwargs):
    stop_for_relaunch()

def _exclude_pids_from_env(*args, **kwargs):
    stop_for_relaunch()

def _caller_ancestor_pids(*args, **kwargs):
    stop_for_relaunch()

def _argv_head_command(*args, **kwargs):
    stop_for_relaunch()

def _is_caller_wrapper_shell(*args, **kwargs):
    stop_for_relaunch()

def _kill_pids_windows(*args, **kwargs):
    stop_for_relaunch()

_POSIX_TERM_GRACE_SECONDS = 10.0

_POSIX_DESCENDANT_GRACE_SECONDS = 2.0

_NO_TTY = ('?', '??', '-')

def _is_detached_session_leader(*args, **kwargs):
    stop_for_relaunch()

def _posix_descendants(*args, **kwargs):
    stop_for_relaunch()

def _wait_gone(*args, **kwargs):
    stop_for_relaunch()

def _kill_pids_posix(*args, **kwargs):
    stop_for_relaunch()

def _kill_stale_dashboard_processes(*args, **kwargs):
    stop_for_relaunch()

def _restart_killed_backends(*args, **kwargs):
    stop_for_relaunch()

def _norm_exe(*args, **kwargs):
    stop_for_relaunch()

def _detect_concurrent_hermes_instances(*args, **kwargs):
    stop_for_relaunch()

def _is_desktop_local_serve_cmdline(*args, **kwargs):
    stop_for_relaunch()

def _flag_value(*args, **kwargs):
    stop_for_relaunch()

def _process_ancestors(*args, **kwargs):
    stop_for_relaunch()

def _process_ppid(*args, **kwargs):
    stop_for_relaunch()

_LOCKFILE_SCHEMA_VERSION = 2

_PROTOCOL_VERSION = 1

_REMOTE_LOCK_SUBDIR = 'desktop-ssh'

BACKEND_LOCK_NAME = 'backend.lock.json'

def _hermes_home_dir(*args, **kwargs):
    stop_for_relaunch()

def _is_hex(*args, **kwargs):
    stop_for_relaunch()

def _valid_lockfile_payload(*args, **kwargs):
    stop_for_relaunch()

def _remote_lock_roots(*args, **kwargs):
    stop_for_relaunch()

def _lock_owned_serve_pids(*args, **kwargs):
    stop_for_relaunch()

def read_valid_backend_lock(*args, **kwargs):
    stop_for_relaunch()

_REAP_MIN_AGE_SECONDS = 180.0

def _process_age_seconds(*args, **kwargs):
    stop_for_relaunch()

def _reap_orphaned_desktop_local_serves(*args, **kwargs):
    stop_for_relaunch()
