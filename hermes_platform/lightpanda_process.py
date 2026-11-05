"""Exact Lightpanda serve identity and quiescence facts; never infer authority from names."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import NamedTuple

import psutil

from hermes_platform.processes import get_process_start_time, pid_exists


class LightpandaCommand(NamedTuple):
    host: str
    port: int
    cache: str | None
    private_networks: bool


def parse_serve(argv):
    """The complete command grammar Hermes actually launches, including unique option values."""
    if len(argv) < 6 or argv[1] != 'serve':
        return None
    values, blocked, index = {}, False, 2
    while index < len(argv):
        key = argv[index]
        if key == '--block-private-networks' and not blocked:
            blocked, index = True, index+1
        elif key in {'--host','--port','--http-cache-dir'} and key not in values and index+1 < len(argv):
            values[key], index = argv[index+1], index+2
        else:
            return None
    try:
        port = int(values.get('--port', ''))
    except (TypeError, ValueError):
        return None
    if values.get('--host') != '127.0.0.1' or not 0 < port <= 65535:
        return None
    cache = values.get('--http-cache-dir')
    if cache is not None and not Path(cache).is_absolute():
        return None
    return LightpandaCommand('127.0.0.1', port, cache, blocked)


def _mapped_executable(child):
    from hermes_platform.host.facts import os_family

    path = Path('/proc')/str(child.pid)/'exe' if os_family() == 'linux' else Path(child.exe())
    info = path.stat()
    return [info.st_dev,info.st_ino]


def process_record(pid, *, executable, argv, home, executable_identity=None):
    expected = parse_serve(argv)
    if expected is None or type(pid) is not int or pid <= 0:
        return None
    try:
        child = psutil.Process(pid)
        started = get_process_start_time(pid)
        if (started is None or child.status() == psutil.STATUS_ZOMBIE
                or child.uids().effective != os.geteuid()
                or Path(child.exe()).resolve() != Path(executable).resolve()):
            return None
        mapped = _mapped_executable(child)
        if executable_identity is None:
            selected = Path(executable).stat()
            executable_identity = [selected.st_dev,selected.st_ino]
        if mapped != executable_identity:
            return None
        actual = child.cmdline()
        if (not actual or Path(actual[0]).resolve() != Path(executable).resolve()
                or actual[1:] != list(argv[1:]) or child.environ().get('HOME') != str(home)):
            return None
        if get_process_start_time(pid) != started:
            return None
        return {'pid':pid, 'start_time':started, 'executable':str(Path(executable).resolve()),
                'argv':list(argv), 'home':str(home), 'port':expected.port, 'executable_identity':mapped}
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ValueError):
        return None


def owned_child_record(supervisor, *, executable, argv, home):
    try:
        parent = psutil.Process(supervisor.pid)
        if parent.uids().effective != os.geteuid():
            return None
        records = [process_record(child.pid, executable=executable, argv=argv, home=home)
                   for child in parent.children(recursive=False)]
        records = [record for record in records if record is not None]
        return records[0] if len(records) == 1 else None
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def matches(record):
    if not isinstance(record, dict):
        return False
    if not all(isinstance(record.get(key), str) for key in ('executable','home')):
        return False
    current = process_record(record.get('pid'), executable=record.get('executable'),
                             argv=record.get('argv', []), home=record.get('home'),
                             executable_identity=record.get('executable_identity'))
    return current == record


def require_cache_idle(cache, *, check_alive):
    """No live same-user serve command may still use this exact shared SQLite/WAL tree."""
    cache = Path(cache).resolve()
    for child in psutil.process_iter(['pid','uids','cmdline','status']):
        check_alive()
        try:
            if child.info['status'] == psutil.STATUS_ZOMBIE:
                continue
            uids = child.info['uids']
            if uids is None or uids.effective != os.geteuid():
                continue
            argv = child.info['cmdline'] or []
            if len(argv) < 2 or argv[1] != 'serve':
                continue
            for index, token in enumerate(argv[2:], 2):
                selected = (argv[index+1] if token == '--http-cache-dir' and index+1 < len(argv)
                            else token.partition('=')[2] if token.startswith('--http-cache-dir=') else None)
                if selected is not None:
                    path = Path(selected)
                    path = path if path.is_absolute() else Path(child.cwd())/path
                    if path.resolve() == cache:
                        raise RuntimeError('A cache producer is still live; shared RAM and archive retained')
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as error:
            raise RuntimeError('A possible Lightpanda cache producer is unreadable; state retained') from error


def _legacy_record(path):
    descriptor = os.open(path, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_size > 65536:
            raise RuntimeError('Legacy Lightpanda owner metadata is unknown; source retained')
        record = json.load(handle)
    if not isinstance(record, dict) or any(type(record.get(key)) is not int or record[key] <= 0
                                           for key in ('pid','owner_pid')):
        raise RuntimeError('Legacy Lightpanda owner metadata is incomplete; source retained')
    return record


def require_legacy_idle(source, *, check_alive):
    """Old control files are conservative absence hints, never executable or signalling authority."""
    for path in sorted(Path(source).glob('*.json')):
        check_alive()
        record = _legacy_record(path)
        if pid_exists(record['owner_pid']) or pid_exists(record['pid']):
            raise RuntimeError('A legacy Lightpanda owner is live or unknown; close it before explicit repair')
    require_cache_idle(Path(source)/'http-cache', check_alive=check_alive)
