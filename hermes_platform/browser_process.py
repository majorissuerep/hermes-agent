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
