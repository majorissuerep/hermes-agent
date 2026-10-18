"""Local interpreter-holder probes plus historical updater import bridges."""
import os
import re
import shlex
from pathlib import Path
from contextlib import suppress
from hermes_cli._old_updater import stop_for_relaunch


_BACKEND_PURPOSES = ("serve", "dashboard")

def _try_call(*args, **kwargs):
    stop_for_relaunch()

def _abort_on_error(*args, **kwargs):
    stop_for_relaunch()

def _planned_stop_marker_path(*args, **kwargs):
    stop_for_relaunch()

def _planned_stop_markers(*args, **kwargs):
    stop_for_relaunch()

def _write_update_planned_stop_marker(*args, **kwargs):
    stop_for_relaunch()

def _wait_for_windows_update_gateway_exit(*args, **kwargs):
    stop_for_relaunch()

def _self_and_non_gateway_ancestor_pids(psutil):
    return {os.getpid(), *(parent.pid for parent in psutil.Process().parents())}

def _cmdline_or_empty(proc) -> str:
    """Joined argv of a psutil process, ``""`` when it can't be read."""
    try:
        return " ".join(proc.cmdline() or [])
    except Exception:
        return ""

def _lower_dir_prefix(path: Path) -> str:
    """``str(path)`` lower-cased with one trailing separator, resolved when possible (prefix matching)."""
    try:
        raw = str(path.resolve())
    except OSError:
        raw = str(path)
    return raw.lower().rstrip(os.sep) + os.sep

def _psutil():
    """The ``psutil`` module, or ``None`` when it can't be imported (callers degrade, never raise)."""
    try:
        import psutil  # type: ignore
    except Exception:
        return None
    return psutil

def _parent_is_live(proc) -> bool:
    """True when *proc* has a running parent that is not a recycled PID (a "parent" created after its child)."""
    parent = proc.parent()
    return parent is not None and parent.is_running() and parent.create_time() <= proc.create_time()

def _detect_venv_python_processes(*, exclude_pids: set[int] | None = None) -> list[tuple[int, str, str]]:
    """Live processes running from the project venv's interpreter as ``(pid, name, cmdline)``; never raises.

    The hermes.exe shim guard misses the Desktop backend and anything off ``venv\\Scripts\\python(w).exe``;
    they keep ``.pyd`` files mapped so a mid-update dependency sync dies half-way. Empty off-Windows / without
    psutil; self + non-gateway ancestors excluded. cmdline/cwd are expensive per process on Windows (500+
    procs can blow the Desktop preflight watchdog), so they are fetched lazily for plausible candidates only.
    The FULL cmdline is kept: callers parse it (the pausable-gateway exemption looks for ``gateway run``).
    """
    from hermes_cli.update_cmd import _m
    psutil = _psutil()
    if psutil is None:
        return []
    from hermes_constants import project_venv_dir
    venv_dir = project_venv_dir(_m().PROJECT_ROOT) or _m().PROJECT_ROOT / "venv"
    venv_prefix = _lower_dir_prefix(venv_dir)
    root_prefix = _lower_dir_prefix(_m().PROJECT_ROOT)
    skip = set(exclude_pids or set()) | _self_and_non_gateway_ancestor_pids(psutil)
    matches: list[tuple[int, str, str]] = []
    try:
        proc_iter = psutil.process_iter(["pid", "exe", "name"])
    except Exception:
        return []
    for proc in proc_iter:
        try:
            info = proc.info
        except Exception:
            continue
        pid, exe = info.get("pid"), info.get("exe")
        if not exe or pid is None or int(pid) in skip:
            continue
        try:
            exe_norm = str(Path(exe).resolve()).lower()
        except (OSError, ValueError):
            exe_norm = str(exe).lower()
        # Primary match: exe lives under this venv (desktop backend / gateway case).
        in_venv = exe_norm.startswith(venv_prefix)
        name = str(info.get("name") or Path(exe).name)
        name_low = name.lower()
        if not (in_venv or name_low.startswith(("python", "pypy")) or name_low in {"uv.exe", "uvx.exe", "hermes.exe"}):
            continue
        cmdline_raw = _cmdline_or_empty(proc)
        cmdline_low = cmdline_raw.lower()
        # Fallback: uv/base-interpreter trampolines have an exe OUTSIDE the venv yet hold
        # its .pyd files — match cmdline (venv path, or `-m hermes_cli.main` + root/cwd).
        if in_venv or venv_prefix in cmdline_low or (
            "hermes_cli.main" in cmdline_low and (root_prefix in cmdline_low or _cwd_prefix(proc).startswith(root_prefix))
        ):
            matches.append((int(pid), name, cmdline_raw))
    return matches

def _cwd_prefix(proc) -> str:
    """Lower-cased cwd of *proc* with one trailing separator; bare ``os.sep`` when unreadable."""
    try:
        return str(proc.cwd() or "").lower().rstrip(os.sep) + os.sep
    except Exception:
        return os.sep

_HOLDER_VALUE_FLAGS_FALLBACK = frozenset({
    "--profile", "-p", "--config", "--model", "-m", "--provider", "--reasoning", "--toolsets", "-t",
    "--skills", "-s", "--continue", "-c", "--resume", "-r", "--oneshot", "-z", "--in", "--usage-file",
})

_holder_value_flags_cache: frozenset | None = None

def _holder_value_flags() -> frozenset:
    """Top-level CLI flags that consume a value, from the REAL parser (nargs != 0); cached per process.

    Derived so the holder classifier can't drift from argparse (a handwritten subset misparsed ``--reasoning high
    serve``). Pre-argparse profile selectors are added explicitly (stripped before argparse sees argv). Falls back
    to a static snapshot when the parser can't import — the updater must classify holders even on a broken tree.

    Introspects ``build_top_level_parser()`` (every option with nargs != 0) so the holder classifier can
    never drift from the argparse surface (#91869 review: a handwritten subset misparsed ``--reasoning high
    serve`` as subcommand ``high`` and ``-m dashboard serve`` as ``dashboard`` — recreating the wrong-hint
    class).
    """
    global _holder_value_flags_cache
    if _holder_value_flags_cache is not None:
        return _holder_value_flags_cache
    flags: set[str] = {"--profile", "-p", "--config"}
    try:
        from hermes_cli._parser import build_top_level_parser
        for action in build_top_level_parser()[0]._actions:
            if action.option_strings and action.nargs != 0:
                flags.update(action.option_strings)
        _holder_value_flags_cache = frozenset(flags)
    except Exception:
        _holder_value_flags_cache = _HOLDER_VALUE_FLAGS_FALLBACK
    return _holder_value_flags_cache

def _hermes_holder_subcommand(cmdline: str) -> str | None:
    """The actual Hermes SUBCOMMAND a venv-holder argv runs, or None (callers must NOT guess a label).

    Token-based, never substring (``kanban --preserve-cache`` contains "serve"): find the ``hermes_cli.main`` /
    ``hermes(.exe)`` entry token, return the first following token that isn't a flag or a flag's value.

    Profile selectors (``--profile X``, ``-p X``) are skipped like the canonical gateway matcher does. See
    #90778.
    """
    try:
        tokens = shlex.split(cmdline, posix=False)
    except Exception:
        tokens = cmdline.split()
    # ``python -c <src> … -m hermes_cli.main <subcommand>``: the entry token belongs to the argv the
    # inline source carries for a LATER spawn, not to this holder (#107002) -- unless the source is a
    # Hermes bootstrap running the entry point in this process (#124318).
    from hermes_platform.process_argv import command_line_runs_inline_source, inline_bootstrap_argv
    normalized = [t.strip("\"'").replace("\\", "/") for t in tokens]
    if command_line_runs_inline_source(normalized):
        tokens = inline_bootstrap_argv(normalized)
        if tokens is None:
            return None

    def _is_entry(i: int, token: str) -> bool:
        low = token.lower().strip("\"'").replace("\\", "/")
        return (low.endswith("hermes_cli.main") and i > 0 and tokens[i - 1] == "-m") or (
            low.rsplit("/", 1)[-1] in ("hermes", "hermes.exe")) or low.endswith("hermes_cli/main.py")

    entry_idx = next((i for i, token in enumerate(tokens) if _is_entry(i, token)), None)
    if entry_idx is None:
        return None
    value_flags = _holder_value_flags()
    i = entry_idx + 1
    while i < len(tokens):
        token = tokens[i]
        if token in value_flags or token.split("=", 1)[0] in value_flags:
            i += 1 if "=" in token else 2  # --flag value consumes two tokens; --flag=value one.
        elif token.startswith("-"):
            i += 1
        else:
            return token.lower()
    return None

def _venv_launcher_ancestors(pids: list[int]) -> list[int]:
    """Venv-interpreter parents of *pids* that hold the install open; never raises.

    A shim-started gateway is a chain: ``venv\\Scripts\\python.exe`` launcher (keeps ``.pyd`` mapped) -> uv
    CPython worker (writes the PID file). The pause set sees the worker, the venv scan sees the launcher, so a
    paused gateway still tripped the guard. One hop up only, venv-prefixed only (bounds blast radius)."""
    from hermes_cli.update_cmd import _m
    psutil = _psutil()
    if not pids or psutil is None:
        return []
    from hermes_constants import project_venv_dir
    venv_dir = project_venv_dir(_m().PROJECT_ROOT) or _m().PROJECT_ROOT / "venv"
    venv_prefix = _lower_dir_prefix(venv_dir)
    skip = _self_and_non_gateway_ancestor_pids(psutil) | set(pids)
    found: list[int] = []
    for pid in pids:
        with suppress(Exception):
            parent = psutil.Process(int(pid)).parent()
            if parent is None:
                continue
            ppid = int(parent.pid)
            if ppid not in skip and ppid not in found and (parent.exe() or "").lower().startswith(venv_prefix):
                found.append(ppid)
    return found

def _venv_holder_kind(cmdline: str) -> str:
    """Machine-readable class of one venv holder for ``--list-venv-holders``.

    ``gateway`` (the pausable gateway matcher), ``backend`` (``serve``/``dashboard`` -- the Desktop
    app's backend shape), ``hermes:<subcommand>`` for any other Hermes entry, else ``python``.
    Derived from the same classifiers the refusal path uses so automation stops exactly what the
    guard would refuse on."""
    subcommand = _hermes_holder_subcommand(cmdline)
    if subcommand:
        return f"hermes:{subcommand}"
    return "python"

VENV_HOLDERS_EXIT = 3  # ``hermes update --list-venv-holders``: holders present (distinct from refusal 2)

def list_venv_holders() -> list[dict]:
    """``[{pid, exe, argv, kind}]`` for every process the venv-holder guard would refuse on, read-only.

    Off Windows (or without psutil) the guard never fires, so the list is empty. ``exe``/``argv`` are the
    live psutil values when readable (the scan may carry only a cmdline prefix)."""
    from hermes_cli.update_cmd import _m
    psutil = _psutil()
    holders: list[dict] = []
    for pid, name, cmdline in _m()._detect_venv_python_processes():
        exe, argv = name, cmdline
        if psutil is not None:
            with suppress(Exception):
                proc = psutil.Process(int(pid))
                exe = proc.exe() or name
                argv = " ".join(proc.cmdline()) or cmdline
        holders.append({"pid": int(pid), "exe": exe, "argv": argv, "kind": _venv_holder_kind(argv)})
    return holders

def _leftover_pausable_gateway_pids(*args, **kwargs):
    stop_for_relaunch()

def _refuse_gateway_ancestor_tree_kill(*args, **kwargs):
    stop_for_relaunch()

def _ledger_manual_serve_holders(*args, **kwargs):
    stop_for_relaunch()

def _serve_relaunch_commands(*args, **kwargs):
    stop_for_relaunch()

def _relaunch_stopped_serves(*args, **kwargs):
    stop_for_relaunch()

def _is_backend_argv(*args, **kwargs):
    stop_for_relaunch()

def _live_argv_low(*args, **kwargs):
    stop_for_relaunch()

def _orphaned_desktop_backend_pids(*args, **kwargs):
    stop_for_relaunch()

def _ledger_reapable_backend_pids(*args, **kwargs):
    stop_for_relaunch()

def _handoff_reapable_backend_pids(*args, **kwargs):
    stop_for_relaunch()

def _stop_process_trees(*args, **kwargs):
    stop_for_relaunch()

def _looks_like_desktop_control_plane(*args, **kwargs):
    stop_for_relaunch()

def _desktop_owns_gateway_lifecycle(*args, **kwargs):
    stop_for_relaunch()

def _win_service(*args, **kwargs):
    stop_for_relaunch()

def _sc_exe(*args, **kwargs):
    stop_for_relaunch()

def _original_process_is_alive(*args, **kwargs):
    stop_for_relaunch()

def _poll_until(*args, **kwargs):
    stop_for_relaunch()

def _process_create_time(*args, **kwargs):
    stop_for_relaunch()

def _verify_service_identities(*args, **kwargs):
    stop_for_relaunch()

def _stop_windows_gateway_service(*args, **kwargs):
    stop_for_relaunch()

def _start_windows_gateway_service(*args, **kwargs):
    stop_for_relaunch()

def _restore_windows_gateway_service(*args, **kwargs):
    stop_for_relaunch()

def _windows_cold_start_plan(*args, **kwargs):
    stop_for_relaunch()

def _pause_windows_gateway_services(*args, **kwargs):
    stop_for_relaunch()

def _owned_gateway_pids(*args, **kwargs):
    stop_for_relaunch()

def _discover_windows_gateways(*args, **kwargs):
    stop_for_relaunch()

def _request_socket_pauses(*args, **kwargs):
    stop_for_relaunch()

def _gateway_drain_timeout(*args, **kwargs):
    stop_for_relaunch()

def _pause_windows_gateways_for_update(*args, **kwargs):
    stop_for_relaunch()

def _cold_start_pause_token(*args, **kwargs):
    stop_for_relaunch()

def _stop_windows_gateways(*args, **kwargs):
    stop_for_relaunch()

def _record_attested_cold_start_profiles(*args, **kwargs):
    stop_for_relaunch()

def _cold_start_attested_profiles(*args, **kwargs):
    stop_for_relaunch()

def _cold_start_windows_gateway_after_update(*args, **kwargs):
    stop_for_relaunch()

def _refresh_windows_gateway_launchers(*args, **kwargs):
    stop_for_relaunch()

def _refresh_bootstrap_cache_scripts(*args, **kwargs):
    stop_for_relaunch()

def _service_gateway_ready(*args, **kwargs):
    stop_for_relaunch()

def _service_running(*args, **kwargs):
    stop_for_relaunch()

def _resume_windows_services(*args, **kwargs):
    stop_for_relaunch()

def _relaunch_paused_gateways(*args, **kwargs):
    stop_for_relaunch()

def _pending_relaunch_pids(*args, **kwargs):
    stop_for_relaunch()

def _relaunch_verify_timeout_s(*args, **kwargs):
    stop_for_relaunch()

def _unmapped_ready_filter(*args, **kwargs):
    stop_for_relaunch()

def _poll_until_ready(*args, **kwargs):
    stop_for_relaunch()

def _verify_relaunched_gateways_alive(*args, **kwargs):
    stop_for_relaunch()

def _resume_windows_gateways_after_update(*args, **kwargs):
    stop_for_relaunch()

def _resume_unless_torn(*args, **kwargs):
    stop_for_relaunch()

def _resume_paused_set(*args, **kwargs):
    stop_for_relaunch()

def _resume_windows_gateways_and_merge_outcome(*args, **kwargs):
    stop_for_relaunch()
