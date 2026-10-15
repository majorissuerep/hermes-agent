"""Venv-holder sweep + gateway lifecycle helpers for ``hermes update`` (POSIX; Windows-service paths are policy no-ops).

Split out of ``update_cmd.py``; names are re-imported there so ``hermes_cli.update_cmd.<name>`` still resolves/monkeypatches.
Origin helpers are imported lazily per function (no cycle; test patches on the origin stay effective).
"""

import logging

from contextlib import contextmanager, nullcontext, redirect_stdout, suppress

import os

import re

import shlex

import subprocess

import sys

import time as _time

from datetime import datetime, timezone, UTC

from pathlib import Path

from hermes_cli.update_cmd_common import _best_effort

logger = logging.getLogger("hermes_cli.update_cmd")  # log-record parity with the origin module

_BACKEND_PURPOSES = ("serve", "dashboard")

def _try_call(fn, log_message: str, *log_args, default=None):
    """``fn()``, or *default* after logging the exception at debug (``log_message`` gets ``*log_args, exc``)."""
    try:
        return fn()
    except Exception as exc:
        logger.debug(log_message, *log_args, exc)
        return default

@contextmanager
def _abort_on_error(prefix: str):
    """Re-raise any failure of a mandatory step as ``RuntimeError(f"{prefix}: {exc}")`` (chained)."""
    try:
        yield
    except Exception as exc:
        raise RuntimeError(f"{prefix}: {exc}") from exc

def _planned_stop_marker_path(profile_path: Path) -> Path:
    return Path(profile_path) / ".gateway-planned-stop.json"

def _planned_stop_markers(running_pids, profile_processes, service_gateway_pids) -> dict:
    """``{pid: marker path}`` for each gateway the socket pause will ask (profile-mapped, not SCM)."""
    markers = {}
    for pid in running_pids:
        home = None if pid in service_gateway_pids else getattr(profile_processes.get(pid), "path", None)
        if home:
            markers[pid] = _planned_stop_marker_path(Path(home))
    return markers

def _write_update_planned_stop_marker(profile_path: Path, pid: int) -> bool:
    """Write a planned-stop marker into a specific profile home."""
    try:
        from gateway.status import _get_process_start_time
        from utils import atomic_json_write
        atomic_json_write(
            _planned_stop_marker_path(profile_path),
            {"target_pid": pid, "target_start_time": _get_process_start_time(pid), "stopper_pid": os.getpid(),
             "written_at": datetime.now(UTC).isoformat()},
            indent=None, separators=(",", ":"),
        )
        return True
    except (OSError, PermissionError):
        return False

def _wait_for_windows_update_gateway_exit(pids: list[int], *, timeout: float) -> set[int]:
    """Wait for the given gateway PIDs to exit, returning survivors."""
    if not pids:
        return set()
    from gateway.status import _pid_exists

    def _alive(pid: int) -> bool:
        try:
            return bool(_pid_exists(pid))
        except Exception:
            return False

    remaining = set(pids)

    def _all_gone() -> bool:
        nonlocal remaining
        remaining = {pid for pid in remaining if _alive(pid)}
        return not remaining

    _poll_until(_all_gone, max(timeout, 0.0), 0.25)
    return {pid for pid in remaining if _alive(pid)}

def _self_and_non_gateway_ancestor_pids(psutil) -> set[int]:
    """PIDs a venv-holder scan must never nominate: this process and its non-gateway ancestry.

    Do NOT blanket-exclude ancestors: under ``/update`` the updater is a CHILD of the gateway, and hiding it
    dead-ends the update on ``venv-blocked``. Gateway ancestors stay visible (the pause path stops them
    gracefully; a detached child survives on Windows); interactive ancestry is never a blocker."""
    _is_gw = None
    with suppress(Exception):
        # Never return ourselves or our own ancestry: a CLI ``hermes update`` runs from the venv python and
        # would otherwise nominate itself. Same #87594 carve-out as _detect_venv_python_processes: a GATEWAY
        # ancestor is not "our own ancestry" in the interactive sense — it is the process the pause
        # machinery must see (the /update-from-gateway topology makes the updater the gateway's child).
        from gateway.status import looks_like_gateway_command_line as _is_gw
    skip: set[int] = {os.getpid()}
    with suppress(Exception):
        for anc in psutil.Process().parents():
            anc_cmdline = _cmdline_or_empty(anc)
            if not (_is_gw is not None and anc_cmdline and _is_gw(anc_cmdline)):
                skip.add(int(anc.pid))
    return skip

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
    from gateway.status_inline_source import command_line_runs_inline_source, inline_bootstrap_argv
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
    from hermes_cli._scan_venv_blockers import _is_pausable_gateway
    if _is_pausable_gateway(cmdline):
        return "gateway"
    subcommand = _hermes_holder_subcommand(cmdline)
    if subcommand in _BACKEND_PURPOSES:
        return "backend"
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

def _leftover_pausable_gateway_pids(matches: list[tuple[int, str, str]]) -> list[int] | None:
    """PIDs from *matches* when EVERY remaining venv holder is a pausable gateway, else ``None`` (keep refusing).

    A gateway respawned inside the pause->guard window (or via an unmapped spawn path) still holds ``.pyd`` files.
    Uses the Desktop preflight's ``_is_pausable_gateway`` so exemption and tolerance cannot drift; live argv is
    re-read via psutil when possible since the scan may hold only a cmdline prefix."""
    from hermes_cli._scan_venv_blockers import _is_pausable_gateway
    psutil = _psutil()
    pids: list[int] = []
    for pid, _name, cmdline in matches:
        argv = cmdline
        if psutil is not None:
            with suppress(Exception):
                argv = " ".join(psutil.Process(int(pid)).cmdline()) or cmdline
        if not _is_pausable_gateway(argv):
            return None
        pids.append(int(pid))
    return pids

def _refuse_gateway_ancestor_tree_kill(pids: list[int], *, gateway_mode: bool) -> bool:
    """Refuse a plain Windows update that would tree-kill its own ancestry (a chat agent's ``hermes update`` is
    a gateway child; ``taskkill /T /F`` kills the updater first). ``--gateway`` is exempt (detached delivery).
    Refuse only when a nominated gateway is positively an ancestor; unknown ancestry keeps existing recovery.

    The leftover holder recovery below uses ``taskkill /T /F`` on Windows, so force-stopping that gateway
    also kills the updater before it can mutate the checkout (#98814).
    """
    if gateway_mode or not pids:
        return False
    def _ancestors():
        from hermes_cli.gateway import _is_pid_ancestor_of_current_process
        return [int(pid) for pid in pids if _is_pid_ancestor_of_current_process(int(pid))]

    ancestors = _try_call(_ancestors, "Could not inspect gateway ancestry before tree-kill: %s")
    if not ancestors:
        return False
    print(
        "✗ Refusing to stop the gateway process tree because this updater "
        f"is running inside it (gateway PID(s): {', '.join(str(pid) for pid in ancestors)}).\n"
        "  On Windows, taskkill /T would terminate the updater before the update can run.\n"
        "  From a chat platform, use `/update` instead.\n  Otherwise, run `hermes update` from a separate terminal."
    )
    return True

def _ledger_manual_serve_holders(matches: list[tuple[int, str, str]]) -> list[dict]:
    """Full ledger entries for venv holders that are MANUAL serve/dashboard backends.

    Positive identity only: self-registered purpose serve/dashboard, live (pid, create_time), recorded spawner
    NOT alive (a Desktop-owned backend keeps its live Electron spawner and must keep the refusal — the app would
    respawn what we kill). Full entries let the relauncher rebuild from host/port/profile, not argv."""
    try:
        from hermes_cli.process_identity import ledger_entries, spawner_is_dead
    except Exception:
        return []
    holder_pids = {int(pid) for pid, _name, _cmd in matches}
    return [
        entry for entry in ledger_entries()
        if entry.get("purpose") in _BACKEND_PURPOSES and isinstance(entry.get("pid"), int) and entry["pid"] in holder_pids
        and spawner_is_dead(entry) is not False  # False = live Desktop supervisor owns it; keep refusing
    ]

def _serve_relaunch_commands(entries: list[dict]) -> list[list[str]]:
    """Rebuild launch commands for stopped serves from ledger host/port/profile — never argv parsing
    (joined argv cannot round-trip Windows paths with spaces). Entries without a port are skipped."""
    from hermes_cli.update_cmd import _m
    hermes = "hermes"
    with suppress(Exception):
        scripts_dir = _m()._venv_scripts_dir()
        if scripts_dir is not None:
            hermes = next((str(scripts_dir / n) for n in ("hermes.exe", "hermes") if (scripts_dir / n).is_file()), hermes)
    commands: list[list[str]] = []
    for entry in entries:
        port = entry.get("port")
        if not isinstance(port, int) or port <= 0:
            continue
        profile, host = str(entry.get("profile") or ""), str(entry.get("host") or "")
        commands.append(
            [hermes] + (["--profile", profile] if profile and profile != "default" else [])
            + [str(entry.get("purpose"))] + (["--host", host] if host else []) + ["--port", str(port)]
        )
    return commands

def _relaunch_stopped_serves(token: dict) -> None:
    """Idempotent atexit relaunch of manual serves stopped by the venv guard.

    `pending` flips False on first invocation so explicit call + atexit registration cannot double-spawn."""
    from hermes_cli.update_cmd import _m, _record_update_step
    if not token.get("pending"):
        return
    token["pending"] = False
    entries = token.get("entries") or []
    if not entries:
        return
    commands = _serve_relaunch_commands(entries)
    skipped = len(entries) - len(commands)
    failed: list = []
    if commands:
        print("  ⟲ Relaunching stopped serve/dashboard backend(s)")
        failed = _m()._respawn_dashboard_processes(commands)
    if skipped or failed:
        print("  ⚠ Some stopped backends could not be relaunched automatically; restart them manually (hermes serve --host <ip> --port <port>).")
    _record_update_step(
        "serve_relaunch", not failed and not skipped,
        f"relaunched={len(commands) - len(failed)} failed={len(failed)} skipped={skipped}",
    )

def _is_backend_argv(argv_low: str) -> bool:
    """Whether an argv is a DESKTOP backend — feeds ``taskkill /T`` via ``_orphaned_desktop_backend_pids``.

    Same predicate as ``_looks_like_desktop_control_plane``: ``-m hermes_cli.main`` entry shape (the
    Desktop's only spawn shape, ``apps/desktop/electron/main.ts``) AND the canonical holder classifier says
    ``serve``/``dashboard``. A user-launched ``hermes.exe serve`` / ``hermes dashboard`` is NOT the
    Desktop's: the guard refuses on it, never reaps it.
    """
    return _looks_like_desktop_control_plane(argv_low)

def _live_argv_low(psutil, pid, cmdline: str) -> str | None:
    """Current lower-cased argv of *pid* (falls back to the scanned *cmdline*); ``None`` if it exited."""
    argv = cmdline
    try:
        argv = " ".join(psutil.Process(int(pid)).cmdline()) or cmdline
    except psutil.NoSuchProcess:
        return None
    except Exception:
        pass
    return argv.lower()

def _orphaned_desktop_backend_pids(matches: list[tuple[int, str, str]]) -> list[tuple[int, int]] | None:
    """``(pid, start_time)`` roots from *matches* when every remaining holder is an ORPHANED backend, else ``None``.

    Killing a Desktop-owned ``serve`` is futile (the app respawns it), but a straggler whose Desktop is gone
    would dead-end the update with "Hermes is still running" and zero open windows. Qualifies only if cmdline
    is a Hermes backend AND the parent is demonstrably gone (PID missing or reused). Tree-aware: holders inside
    an accepted root's tree fold into it; only roots are returned (``taskkill /T`` reaps descendants). Any
    live-parent backend, unjustified non-backend, unprovable case, or no psutil -> ``None``. Never raises.

    The venv-holder guard refuses on the Desktop app's ``serve`` backend by design: while the Desktop is
    open, killing its backend is futile (the app supervises and respawns it within seconds), so the user
    must close the app. But in the GUI-updater handoff path the Desktop has *already exited* — by contract
    it tree-kills its backends and waits for the venv shim before spawning hermes-setup, and the
    update-in-progress marker parks any relaunched Desktop from spawning a fresh backend (#50238). A
    ``serve`` backend still holding the venv at that point is a straggler whose supervisor is gone: SIGTERM
    raced its spawn, or it belongs to a crashed window. Nothing will respawn it, and refusing on it
    dead-ends the update with "Hermes is still running" while the user stares at zero open windows (ryanc's
    2026-08-09 01:59/02:17 failures).
    """
    psutil = _psutil()
    if psutil is None:
        return None
    # Pass 1: find orphaned backend ROOTS among the holders.
    roots: list[tuple[int, int]] = []
    remaining: list[int] = []  # holders still to justify
    for pid, _name, cmdline in matches:
        low = _live_argv_low(psutil, pid, cmdline)
        if low is None:
            continue  # exited between scan and classification — nothing to reap
        if not _is_backend_argv(low):
            remaining.append(int(pid))
            continue
        try:
            proc = psutil.Process(int(pid))
            # Fingerprint from the SAME psutil handle, centisecond-quantized like
            # gateway.status.get_process_start_time so pid_is_hermes round-trips at kill time.
            process_start_time = round(proc.create_time() * 100)
        except psutil.NoSuchProcess:
            continue  # exited during classification — nothing to reap
        except Exception:
            return None
        try:
            ppid = proc.ppid()
            parent = psutil.Process(ppid) if ppid else None
            # PID-reuse check: a "parent" created after its child is a recycled PID. A live parent is
            # not a root but may be an orphan root's descendant (the venv trampoline re-execs uv
            # python with the SAME argv) — defer to pass 2.
            if parent is not None and parent.is_running() and parent.create_time() <= proc.create_time():
                remaining.append(int(pid))
                continue
        except psutil.NoSuchProcess:
            pass  # parent gone → orphan
        except Exception:
            return None
        roots.append((int(pid), process_start_time))
    # Pass 2: every non-backend holder must descend from an accepted orphan root
    # (dies with the tree reap); anything else keeps the refusal.
    root_set = {pid for pid, _start_time in roots}
    for pid in remaining:
        try:
            if not root_set or not root_set & {int(a.pid) for a in psutil.Process(pid).parents()}:
                return None
        except psutil.NoSuchProcess:
            continue  # exited already
        except Exception:
            return None
    return roots

def _ledger_reapable_backend_pids(matches: list[tuple[int, str, str]]) -> list[int]:
    """PIDs the spawn ledger positively identifies as orphaned backends; never raises.

    Strongest rung (no PPID/cmdline inference): qualifies when ``(pid, create_time)`` matches a live ledger entry
    (PID reuse can't forge it), purpose is a REAPABLE kind (never interactive), and the recorded SPAWNER is
    provably dead. Safe in ANY context. Unlisted holders fall to later rungs and never disqualify identified ones."""
    try:
        from hermes_cli.process_identity import REAPABLE_PURPOSES, ledger_entries, spawner_is_dead
        entries = ledger_entries()
    except Exception:
        return []
    by_pid = {e.get("pid"): e for e in entries if isinstance(e.get("pid"), int)}
    return [
        int(pid) for pid, _name, _cmdline in matches
        if (entry := by_pid.get(int(pid))) and entry.get("purpose") in REAPABLE_PURPOSES and spawner_is_dead(entry) is True
    ]

def _handoff_reapable_backend_pids(matches: list[tuple[int, str, str]]) -> list[int] | None:
    """Backend PIDs safe to tree-reap during a GUI-updater hand-off, INCLUDING ones with a live parent; never raises.

    The orphan-only rung bails on ANY live parent (mid-teardown Electron, launcher->worker chain) and hung a
    hand-off. Inside the hand-off gate (marker + ``--gateway`` + no live ``hermes.exe`` shim) nothing legitimate
    supervises a ``serve`` from this venv, so survivors are leaks. Any non-backend holder or no psutil ->
    ``None``. The CALLER must have confirmed the gate; outside it the stricter orphan-only path stands.

    Any ``serve`` backend still holding the venv here is therefore a leak, live parent or not, and reaping
    its tree is correct rather than a race. See #50238.
    """
    psutil = _psutil()
    if psutil is None:
        return None
    roots: list[int] = []
    for pid, _name, cmdline in matches:
        low = _live_argv_low(psutil, pid, cmdline)
        if low is None:
            continue  # exited — nothing to reap
        if not _is_backend_argv(low):
            return None  # unexpected non-backend holder: refuse the whole set
        roots.append(int(pid))
    return roots or None

def _stop_process_trees(pids: list[int] | list[tuple[int, int]]) -> None:
    """Force-stop each PID with its full child tree (Windows); best effort, never raises.

    ``taskkill /T /F``: stopping only the parent can leave a ``.hermes-runtime`` child holding the install open.

    See #70026.
    """
    from gateway.status import get_process_start_time
    from hermes_cli._subprocess_compat import pid_is_hermes, windows_hide_flags
    for entry in pids:
        pid, expected_start_time = entry if isinstance(entry, tuple) else (int(entry), get_process_start_time(int(entry)))
        try:
            if expected_start_time is None:
                logger.debug("Skipping taskkill of PID %s: process identity unavailable", pid)
                continue
            if not pid_is_hermes(pid, expected_start_time=expected_start_time):
                logger.debug("Skipping taskkill of non-Hermes or changed PID %s", pid)
                continue
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"], check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                creationflags=windows_hide_flags(),
            )
        except Exception as exc:
            logger.debug("Could not stop process tree %s: %s", pid, exc)

def _looks_like_desktop_control_plane(cmdline: str) -> bool:
    """True for this-install ``hermes serve`` / ``hermes dashboard`` argv (Desktop control plane).

    Not the messaging gateway — don't feed into ``looks_like_gateway_command_line``. Token-based via the
    parser-derived classifier, never substring (``kanban --preserve-cache``, ``-m dashboard chat``).
    Undeterminable subcommand is NOT a control plane.

    See #92091.
    A cmdline whose subcommand cannot be determined is NOT a control plane — callers must not guess
    ownership. See #90778, #91869.
    """
    return "hermes_cli.main" in (cmdline or "").lower() and _hermes_holder_subcommand(cmdline) in _BACKEND_PURPOSES

def _desktop_owns_gateway_lifecycle() -> bool:
    """True when Desktop currently supervises this install's control plane (updater must not steal gateway start).

    Not proof messaging is served: serve is the control plane, the gateway a detached sibling. Prefer the spawn
    ledger; fall back to the venv-holder scan. An orphaned control plane (supervisor gone) does not count;
    without psutil orphanhood is unprovable and a live control plane suffices.

    See #76129, #92091.
    """
    from hermes_cli.update_cmd import _m
    with _best_effort('Desktop-lifecycle ledger probe failed: %s'):
        from hermes_cli.process_identity import ledger_entries, spawner_is_dead
        if any(e.get("purpose") in _BACKEND_PURPOSES and spawner_is_dead(e) is False for e in ledger_entries()):
            return True
    psutil = _psutil()
    for pid, _name, cmdline in _try_call(_detect_venv_python_processes, "Desktop-lifecycle holder scan failed: %s") or []:
        if not _looks_like_desktop_control_plane(cmdline):
            continue
        if psutil is None:
            return True
        with suppress(Exception):
            if _parent_is_live(psutil.Process(int(pid))):
                return True
    return False

def _win_service(name: str):
    """No-op: native Windows support removed from this fork (WSL is the Windows answer)."""
    return

def _sc_exe(verb: str, name: str, service, settled_status: str) -> None:
    """No-op: native Windows support removed from this fork (WSL is the Windows answer)."""
    return

def _original_process_is_alive(psutil, pid: int, create_time: float) -> bool:
    """Is the process with this exact ``(pid, create_time)`` identity still alive? AccessDenied/unknown reads
    True (fail closed: the venv may still be locked)."""
    try:
        current = float(psutil.Process(pid).create_time())
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except Exception:
        return True
    return abs(current - create_time) <= 0.001

def _poll_until(condition, timeout: float, interval: float = 0.2) -> bool:
    """Poll *condition* every *interval* seconds until true (True) or *timeout* elapses (False)."""
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if condition():
            return True
        _time.sleep(interval)
    return False

def _process_create_time(psutil, pid: int, label: str) -> float:
    """``create_time`` of *pid*; unreadable identity aborts the stop (RuntimeError)."""
    try:
        return float(psutil.Process(int(pid)).create_time())
    except Exception as exc:
        raise RuntimeError(f"Windows {label} process identity is unavailable before stop") from exc

def _verify_service_identities(psutil, name: str, service, expected_service_identity, expected_gateway_identity) -> None:
    """No-op: native Windows support removed from this fork (WSL is the Windows answer)."""
    return

def _stop_windows_gateway_service(*args, **kwargs):
    """No-op: native Windows support removed from this fork (WSL is the Windows answer)."""
    return

def _start_windows_gateway_service(name: str, *, timeout: float = 30.0) -> None:
    """No-op: native Windows support removed from this fork (WSL is the Windows answer)."""
    return

def _restore_windows_gateway_service(name: str, *, timeout: float = 60.0) -> None:
    """No-op: native Windows support removed from this fork (WSL is the Windows answer)."""
    return

def _windows_cold_start_plan() -> dict | None:
    """No-op: native Windows support removed from this fork (WSL is the Windows answer)."""
    return None

class ServicePauseFailed(RuntimeError):
    """A gateway service could not be stopped; ``rollback_failures`` lists what the in-line
    rollback could not restart. Empty = everything stopped was restarted, so the pause is void;
    otherwise its record must stay, since gateways are still down."""

    def __init__(self, detail: str, rollback_failures: list[str]):
        super().__init__(detail)
        self.rollback_failures = rollback_failures

def _pause_windows_gateway_services(service_gateways, token: dict, profiles: dict, unmapped: list) -> dict:
    """No-op: native Windows support removed from this fork (WSL is the Windows answer)."""
    return None

def _owned_gateway_pids(pids, *, keep=(), quiet: bool = True) -> list[int]:
    """*pids* whose live home this update owns, plus *keep* (PIDs mapped to this install's profile
    PID files / services). The same home scope the POSIX fleet restart uses (#93349): a gateway of
    another Hermes install, or one whose home cannot be read, is named (unless *quiet*) and left
    running, never paused, force-killed or replayed (#124659)."""
    from hermes_cli.update_cmd_fleet import _scoped_manual_gateway_pids
    return _scoped_manual_gateway_pids(list(pids), keep=keep, quiet=quiet)

def _discover_windows_gateways():
    """``(profile_processes, service_gateways, service_gateway_pids, running_pids)`` for the pause; any indeterminate probe aborts."""
    from hermes_cli.gateway import find_gateway_pids, find_profile_gateway_processes, find_windows_gateway_services
    with _abort_on_error("Could not map Windows gateway PIDs to profiles"):
        profile_process_list = find_profile_gateway_processes(strict=True)
        profile_processes = {proc.pid: proc for proc in profile_process_list}
    with _abort_on_error("Could not determine Windows gateway service ownership"):
        service_gateways = find_windows_gateway_services(profile_processes=profile_process_list)
    service_gateway_pids = {int(service.gateway_pid) for service in service_gateways}
    with _abort_on_error("Could not discover Windows gateway PIDs before update"):
        # find_gateway_pids(all_profiles=True) is a HOST-wide scan; only this install's fleet is paused.
        running_pids = _owned_gateway_pids(dict.fromkeys(
            [*find_gateway_pids(all_profiles=True), *sorted(profile_processes), *sorted(service_gateway_pids)]
        ), keep=set(profile_processes) | service_gateway_pids, quiet=False)
    return profile_processes, service_gateways, service_gateway_pids, running_pids

def _request_socket_pauses(running_pids, profile_processes, service_gateway_pids, on_request=None):
    """Marker + socket-first pause for every profile-mapped gateway; ``(profiles, mapped_pids, socket_acks)``.

    Socket ACK = the gateway drains and exits by its own graceful path. No answer (older
    gateway) -> the marker poll / force-kill ladder in the caller. ``on_request(pid)`` runs once the
    request is issued (marker on disk, else a positive socket ACK) and before the socket call: from
    then on a crash leaves that gateway owed while it drains, never before."""
    profiles: dict[str, int] = {}
    mapped_pids = []
    socket_acks: list[dict] = []
    for pid in running_pids:
        proc = None if pid in service_gateway_pids else profile_processes.get(pid)
        if proc is None:
            continue
        profiles[str(proc.profile)] = int(pid)
        mapped_pids.append(int(pid))
        requested = _write_update_planned_stop_marker(Path(proc.path), int(pid))
        if requested and on_request is not None:
            on_request(int(pid))
        try:
            # Socket-first pause (#92091 step 2): ask the gateway to drain and exit itself instead of
            # relying on the marker poll + force-kill ladder. A positive ACK means the gateway is running
            # its own graceful restart path (same drain as SIGUSR1/service restarts) and will release its
            # venv handles on the way out. No answer (older gateway, no socket) → the marker watcher /
            # force-kill fallback below behaves exactly as before this verb existed.
            from gateway.control_socket import pause_gateway_for_update
            ack = pause_gateway_for_update(Path(proc.path))
            if ack and (ack.get("pausing") or ack.get("already_stopping")):
                socket_acks.append(ack)
                if not requested and on_request is not None:
                    on_request(int(pid))
        except Exception as exc:
            logger.debug("Socket pause unavailable for gateway %s: %s", pid, exc)
    return profiles, mapped_pids, socket_acks

def _gateway_drain_timeout(socket_acks: list[dict]) -> float:
    """Drain budget: configured restart drain (>= 1s), raised to a socket-paused gateway's declared
    ACTIVE-TURN budget + teardown grace so it isn't force-killed mid-turn."""
    from hermes_cli.gateway import _get_restart_drain_timeout
    try:
        drain_timeout = max(float(_get_restart_drain_timeout()), 1.0)
    except Exception:
        drain_timeout = 10.0
    if socket_acks:
        with suppress(Exception):
            declared = max(float(a.get("drain_timeout") or 0.0) for a in socket_acks)
            drain_timeout = max(drain_timeout, declared + 10.0)
        print(f"  → {len(socket_acks)} gateway(s) ACKed socket pause; waiting up to {int(drain_timeout)}s for graceful exit")
    return drain_timeout

def _pause_windows_gateways_for_update() -> dict | None:
    """No-op: native Windows support removed from this fork (WSL is the Windows answer)."""
    return None

def _cold_start_pause_token(adopted: dict | None, claims: list) -> dict | None:
    """No gateway runs: the cold-start plan, plus any orphaned set adopted from a killed update."""
    from hermes_cli import update_pause_record as pause_record
    token = _windows_cold_start_plan()
    # Other profiles may hold a dead attestation even when the active profile owes nothing
    # (clean exit, autostart not installed): give them a token to ride on.
    probe = token if token is not None else {"resume_needed": True, "profiles": {}, "unmapped_pids": [], "unmapped": []}
    _record_attested_cold_start_profiles(probe, set())
    token = probe if probe.get("cold_start_profiles") else token
    if adopted is None:
        return pause_record.stamp_tree(token) if token is not None else None
    token = token if token is not None else {"resume_needed": True, "profiles": {}, "unmapped_pids": [], "unmapped": []}
    with _abort_on_error("Could not record the adopted paused-gateway set"):
        return pause_record.record_pause(token, adopted, claims)

def _stop_windows_gateways(*args, **kwargs):
    """No-op: native Windows support removed from this fork (WSL is the Windows answer)."""
    return

def _record_attested_cold_start_profiles(token: dict, running_profiles: set) -> None:
    """No-op: native Windows support removed from this fork (WSL is the Windows answer)."""
    return

def _cold_start_attested_profiles(token: dict) -> None:
    """No-op: native Windows support removed from this fork (WSL is the Windows answer)."""
    return

def _cold_start_windows_gateway_after_update(token: dict | None = None) -> bool:
    """No-op: native Windows support removed from this fork (WSL is the Windows answer)."""
    return False

def _refresh_windows_gateway_launchers() -> None:
    """No-op: native Windows support removed from this fork (WSL is the Windows answer)."""
    return

def _refresh_bootstrap_cache_scripts(branch: str = "main") -> None:
    """Heal mutable POSIX installer-cache entries; leave immutable commit pins alone."""
    from hermes_cli.update_cmd import _m
    safe_ref = re.sub(r"[^A-Za-z0-9._-]", "_", str(branch or "main"))
    if re.fullmatch(r"[0-9a-fA-F]{7,40}", safe_ref):
        return
    cached = Path(_m().get_hermes_home()) / "bootstrap-cache" / f"install-{safe_ref}.sh"
    source = _m().PROJECT_ROOT / "scripts" / "install.sh"
    if not cached.is_file() or not source.is_file():
        return
    try:
        data = source.read_bytes()
        if cached.read_bytes() != data:
            staged = cached.with_suffix(".sh.tmp")
            staged.write_bytes(data)
            os.replace(staged, cached)
            print(f"  ✓ Refreshed installer bootstrap-cache script: {cached.name}")
    except OSError as exc:
        logger.debug("Installer cache refresh failed: %s", exc)

_SERVICE_READY_TIMEOUT_S = 60.0

_SERVICE_READY_PROBE_S = 5.0

_SERVICE_RETRY_BASE_S = 60.0

_SERVICE_RETRY_MAX_S = 3600.0

def _service_gateway_ready(name: str, profile: str | None, timeout_s: float | None = None) -> list[int]:
    """No-op: native Windows support removed from this fork (WSL is the Windows answer)."""
    return None

def _service_running(name: str) -> bool:
    """No-op: native Windows support removed from this fork (WSL is the Windows answer)."""
    return None

def _resume_windows_services(token: dict) -> None:
    """No-op: native Windows support removed from this fork (WSL is the Windows answer)."""
    return

def _relaunch_paused_gateways(profiles: dict, unmapped: list) -> tuple[dict, list]:
    """No-op: native Windows support removed from this fork (WSL is the Windows answer)."""
    return None

_RELAUNCH_VERIFY_TIMEOUT_S = 30.0

def _pending_relaunch_pids(profiles: dict, unmapped: list, pid_exists) -> list[int]:
    """Old PIDs a restart watcher is still waiting on, sorted; empty when every relaunch can have run.

    ``_spawn_gateway_restart_watcher`` respawns the gateway only once the PID it was handed is gone,
    so while any of them is alive the relaunch has provably not started yet. Pure function of data
    (``pid_exists`` is injected) so the decision is testable off Windows.
    """
    candidates = [int(pid) for pid in profiles.values()]
    candidates += [int(entry["pid"]) for entry in unmapped if entry.get("argv") and entry.get("pid")]
    return sorted({pid for pid in candidates if pid > 0 and pid_exists(pid)})

def _relaunch_verify_timeout_s(profiles: dict, unmapped: list, pid_exists) -> float:
    """Liveness budget for the post-relaunch poll.

    The base window assumes the watchers respawn immediately. When an old PID is still alive the
    watcher is still in its wait loop, so the poll must reach at least the watcher's own deadline —
    otherwise ``hermes update`` declares "no stable gateway process appeared" for a gateway that was
    never scheduled to appear inside the window (#107002).
    """
    from hermes_cli.gateway import GATEWAY_RESTART_WATCHER_TIMEOUT_S
    if not _pending_relaunch_pids(profiles, unmapped, pid_exists):
        return _RELAUNCH_VERIFY_TIMEOUT_S
    return float(GATEWAY_RESTART_WATCHER_TIMEOUT_S) + _RELAUNCH_VERIFY_TIMEOUT_S

def _unmapped_ready_filter(entry: dict, taken: set):
    """``pid_filter`` matching the gateway an unmapped entry's replay started: a gateway running the
    same arguments after the (normalized) interpreter on the entry's home, born after the process it
    replaces, not one another entry already vouched for. Two homes can run identical selectorless argv,
    so argv alone would let home B's gateway retire home A's debt (a service gateway on another home
    included; on A's home only one gateway can run). Only an entry recorded before ``home`` was
    captured (no key: a pre-durable-pause updater's token) keeps the argv-only match; a recorded
    ``home`` that is empty proves no runtime and is never matched."""
    import psutil
    from gateway.status import _looks_like_gateway_process, _same_hermes_home, get_process_start_time
    from hermes_cli.update_fleet_scope import gateway_pid_home
    tail, old = list(entry.get("argv") or [])[1:], int(entry.get("pid") or 0)
    home, old_ct = entry.get("home"), entry.get("ct")

    def same_runtime(pid: int) -> bool:
        if "home" not in entry:
            return True
        if not home:
            return False
        live_home, ct = gateway_pid_home(pid), get_process_start_time(pid)
        return live_home is not None and _same_hermes_home(live_home, home) and (old_ct is None or ct is None or ct > old_ct)

    def matches(pid: int) -> bool:
        if pid == old or pid in taken:
            return False
        try:
            return (list(psutil.Process(pid).cmdline() or [])[1:] == tail and _looks_like_gateway_process(pid)
                    and same_runtime(pid))
        except (psutil.Error, OSError, ValueError):  # exited, access denied, zombie, invalid pid
            return False
    return lambda pids: [pid for pid in _owned_gateway_pids(pids) if matches(int(pid))]

_READY_INTERVAL_S = 0.4  # gateway_windows._wait_for_gateway_ready's poll interval and

_READY_CONFIRM_S = 2.0   # confirmation window: one hit proves a spawn, not a survived startup

def _poll_until_ready(targets: list, deadline: float, taken: set) -> dict:
    """``{key: pids}`` for each ``(key, probe, claim_all)`` target whose gateway stayed visible for
    a whole confirmation window. All targets share one loop, so N targets cost one window, not N:
    one fleet process scan per tick (``probe(scan)`` calls ``scan()`` only if it needs the fleet),
    and each target is credited only on its own hits. *taken* holds, while a target is probed, the
    PIDs earlier targets vouched for (all of a profile's, the first of an unmapped entry's) so one
    gateway never retires two debts. A hit provisional at the deadline still finishes its window;
    a probe that raises leaves its target unready."""
    from hermes_cli.gateway import find_gateway_pids
    ready: dict = {}
    since: dict = {}
    failed: set = set()
    while True:
        now = _time.monotonic()
        pending = [t for t in targets if t[0] not in ready and t[0] not in failed and (now < deadline or t[0] in since)]
        if not pending:
            return ready
        fleet: list = []

        def scan() -> list:
            if not fleet:
                fleet.append(list(find_gateway_pids(all_profiles=True)))
            return fleet[0]
        taken.clear()
        taken.update(pid for pids in ready.values() for pid in pids)
        for key, probe, claim_all in pending:
            try:
                pids = list(probe(scan))
            except Exception as exc:  # health: allow BLE001 -- one target's probe failure must not stall the others
                logger.warning("Could not verify Windows gateway %s after update: %s", key, exc)
                failed.add(key)
                continue
            if not pids:
                since.pop(key, None)
                continue
            vouched = pids if claim_all else pids[:1]
            taken.update(vouched)
            if now - since.setdefault(key, now) >= _READY_CONFIRM_S:
                ready[key] = vouched
        if len(ready) + len(failed) < len(targets):
            _time.sleep(_READY_INTERVAL_S)

def _verify_relaunched_gateways_alive(token: dict, profiles: dict, unmapped: list) -> None:
    """No-op: native Windows support removed from this fork (WSL is the Windows answer)."""
    return

def _resume_windows_gateways_after_update(token: dict | None) -> None:
    """Settle the shared pause token, including Linux/macOS pre-swap pauses."""
    if not token or not token.get("resume_needed"):
        return
    import atexit
    atexit.unregister(_resume_windows_gateways_after_update)
    if token.get("platform") != "posix":
        token["resume_needed"] = False
        return
    with redirect_stdout(sys.stderr) if token.get("recovery") else nullcontext():
        _resume_unless_torn(token)

def _resume_unless_torn(token: dict) -> None:
    """Resume the paused set, or leave it owed when the checkout is not whole."""
    from hermes_cli import update_pause_record as pause_record
    if token.get("pause_id"):
        whole, why = pause_record.tree_is_whole(token)
        if not whole:
            # Starting gateways now would boot them on a half-written tree or the pre-sync
            # dependencies. The durable record keeps the obligation for the next launch, which
            # runs after the interrupted-pull restore and the dependency sync.
            print(f"  ⚠ Paused gateway(s) left stopped: {why}.\n"
                  "    The next `hermes` command (or `hermes update`) restarts them.")
            token["resume_needed"] = False
            token["resume_deferred"] = why
            pause_record.sync(token)
            return
    try:
        _resume_paused_set(token)
    finally:
        pause_record.sync(token)

def _resume_paused_set(token: dict) -> None:
    """Bring every paused runtime back; each one is retired from *token* only on its own verified
    start, and one failure (an SCM service, a profile) never keeps the others stopped."""
    from hermes_cli.update_cmd import _m
    if token.get("platform") == "posix":  # Linux/macOS pre-swap pause (update_cmd_posix_pause)
        from hermes_cli.update_cmd_posix_pause import resume_paused_set
        return resume_paused_set(token)
    # Regenerate launcher scripts before respawning so a legacy pythonw-era
    # autostart entry comes back on the current design at next login too.
    _m()._refresh_windows_gateway_launchers()
    failures: list[str] = []

    def attempt(step) -> None:
        try:
            step()
        except Exception as exc:  # health: allow BLE001 -- per-step isolation: any failed step is recorded and re-raised as a summary so it never keeps the other paused runtimes stopped
            failures.append(str(exc))

    attempt(lambda: _resume_windows_services(token))
    profiles = dict(token.get("profiles") or {})
    unmapped = list(token.get("unmapped") or [])
    if profiles or any(u.get("argv") for u in unmapped):
        launched, launched_unmapped = _relaunch_paused_gateways(profiles, unmapped)
        if launched or launched_unmapped:
            attempt(lambda: _verify_relaunched_gateways_alive(token, launched, launched_unmapped))
        else:
            token["relaunched_profiles"] = []
        not_launched = sorted(set(profiles) - set(launched)) + [
            f"pid {u.get('pid')}" for u in unmapped if u not in launched_unmapped]
        if not_launched:
            failures.append("Could not restart every paused Windows gateway: " + ", ".join(not_launched))
    elif token.get("cold_start_if_installed"):
        # Before the per-profile spawns: this guard is fleet-wide (any live gateway ⇒ done).
        def cold_start() -> None:
            if not _m()._cold_start_windows_gateway_after_update(token):
                raise RuntimeError("Windows gateway cold-start was not verified")
            token["cold_start_if_installed"] = False
        attempt(cold_start)
    # After the paused profiles are back: a dead-attested sibling that fails to cold-start must not
    # keep the profiles that WERE running from being relaunched.
    attempt(lambda: _cold_start_attested_profiles(token))
    if failures:
        raise RuntimeError("; ".join(failures))
    token["resume_needed"] = False

def _resume_windows_gateways_and_merge_outcome(outcome, _windows_gateway_resume, gateway_mode: bool):
    """Resume the pre-swap paused set; a restart debt cannot fail committed code."""
    from hermes_cli.update_cmd import _m
    try:
        _m()._resume_windows_gateways_after_update(_windows_gateway_resume)
    except Exception as exc:
        outcome.incomplete = True
        outcome.phase_errors.append(str(exc))
        logger.warning("Gateway restart incomplete after update", exc_info=True)
        print(f"  ⚠ Gateway restart incomplete: {exc}")

