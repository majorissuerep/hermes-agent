"""Exact native browser-client command and daemon identity facts; no secret data returned."""
from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

import psutil

from hermes_platform.processes import get_process_start_time


class AgentBrowserCommand(NamedTuple):
    prefix: tuple[str, ...]
    action: str
    arguments: tuple[str, ...]


def parse_agent_browser_command(argv) -> AgentBrowserCommand | None:
    """Hermes's concrete client prefix/action, refusing unknown option grammars."""
    values = {'--session', '--cdp', '--engine'}
    switches = {'--json', '--headed'}
    index = 1
    while index < len(argv):
        token = argv[index]
        if token in values:
            index += 2
        elif token in switches:
            index += 1
        elif token.startswith('-'):
            return None
        else:
            return AgentBrowserCommand(tuple(argv[:index]), token, tuple(argv[index+1:]))
    return None


def browser_process_record(pid, *, executable, socket_dir) -> dict | None:
    """A stable same-user PID running the exact selected executable and original IPC target."""
    if executable is None or type(pid) is not int or pid <= 0:
        return None
    try:
        process = psutil.Process(pid)
        started = get_process_start_time(pid)
        if started is None or process.status() == psutil.STATUS_ZOMBIE:
            return None
        if process.uids().effective != os.geteuid() or Path(process.exe()).resolve() != Path(executable).resolve():
            return None
        selected = (process.environ() or {}).get('AGENT_BROWSER_SOCKET_DIR')
        if selected is None or os.path.normpath(selected) != str(Path(socket_dir)):
            return None
        if get_process_start_time(pid) != started:
            return None
        return {'pid':pid, 'start_time':started}
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ValueError):
        return None


def harness_process_record(pid_path, *, runtime, name, expected_env=None) -> dict | None:
    """The exact Python module, runtime and name of the installed harness daemon."""
    import stat
    import sys

    try:
        descriptor = os.open(pid_path, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_size > 64:
                return None
            pid = int(handle.read(65))
        if pid <= 0:
            return None
        child = psutil.Process(pid)
        started = get_process_start_time(pid)
        if (started is None or child.status() == psutil.STATUS_ZOMBIE
                or child.uids().effective != os.geteuid() or Path(child.exe()).resolve() != Path(sys.executable).resolve()):
            return None
        argv = child.cmdline()
        if len(argv) != 3 or argv[1:] != ['-m', 'browser_harness.daemon']:
            return None
        env = child.environ()
        if env.get('BH_RUNTIME_DIR') != str(runtime) or env.get('BU_NAME', 'default') != name:
            return None
        selected = ('HOME','BH_HOME','BH_CONFIG_DIR','BH_RUNTIME_DIR_SHARED','BU_CDP_URL','BU_CDP_WS')
        if expected_env is not None and any(env.get(key) != expected_env.get(key) for key in selected):
            return None
        return {'pid':pid, 'start_time':started} if get_process_start_time(pid) == started else None
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ValueError):
        return None


def require_harness_source_idle(source, *, check_alive):
    """Refuse an actual installed harness CLI/daemon referencing this legacy source."""
    source = Path(source)
    for process in psutil.process_iter(['pid', 'uids', 'cmdline']):
        check_alive()
        try:
            identities = process.info['uids']
            if identities is None or identities.effective != os.geteuid():
                continue
            argv = process.info['cmdline'] or []
            if len(argv) != 3 or argv[1] != '-m' or argv[2] not in {'browser_harness.run', 'browser_harness.daemon'}:
                continue
            env = process.environ()
            home = Path(env.get('HOME') or Path.home())
            selected_home = env.get('BH_HOME') or env.get('BROWSER_HARNESS_HOME')
            base = Path(selected_home) if selected_home else Path(env.get('XDG_CONFIG_HOME') or home/'.config')/'browser-harness'
            roots = [base, Path(env.get('BH_CONFIG_DIR') or base),
                     Path(env.get('BH_AGENT_WORKSPACE') or base/'agent-workspace')]
            for root in roots:
                root = root.expanduser().resolve()
                if root == source or root.is_relative_to(source) or source.is_relative_to(root):
                    raise RuntimeError('An original Browser Use process is live; close it before importing source data')
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as exc:
            raise RuntimeError('A possible Browser Use owner cannot be inspected; original source retained') from exc


def _harness_pid_absent(pid_path):
    import stat

    try:
        descriptor = os.open(pid_path, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        with os.fdopen(descriptor, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_size > 64:
                return False
            pid = int(handle.read(65))
        return pid > 0 and psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True
    except (psutil.AccessDenied, OSError, ValueError):
        return False


def harness_runtime_absent(pid_path, *, runtime, name):
    """Native PID absence and no exact daemon process, including stale PID files after exit."""
    if not _harness_pid_absent(pid_path):
        return False
    for process in psutil.process_iter(['uids','cmdline']):
        try:
            identities = process.info['uids']
            if identities is None or identities.effective != os.geteuid():
                continue
            argv = process.info['cmdline'] or []
            if len(argv) != 3 or argv[1:] != ['-m','browser_harness.daemon']:
                continue
            env = process.environ()
            if env.get('BH_RUNTIME_DIR') == str(runtime) and env.get('BU_NAME','default') == name:
                return False
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            return False
    return True
