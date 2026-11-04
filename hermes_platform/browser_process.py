"""Exact native browser-client command and daemon identity facts; no secret data returned."""
from __future__ import annotations

import os
from pathlib import Path

import psutil

from hermes_platform.processes import get_process_start_time


def agent_browser_action(argv) -> str | None:
    """The action in Hermes's concrete agent-browser argv, refusing unknown option grammars."""
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
            return token
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
