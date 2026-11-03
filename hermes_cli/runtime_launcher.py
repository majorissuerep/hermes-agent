"""Credential-free commands bound to the running installation's source.

A published command is public code, but its directory is an *active* private
path: children of a live owner execute it for that owner's whole lifetime. It
therefore lives outside ``cache/scratch``, whose idle pruning would remove it
from under a long-running owner. Each directory names its owner by PID, start
time and PID namespace; only a provably dead owner's directory is swept.
"""
from __future__ import annotations

import atexit
import contextlib
from functools import cache
import os
from pathlib import Path
import shlex
import shutil
import stat
import sys
import tempfile
from typing import Iterator


def write_cli(directory: Path, source: Path) -> Path:
    """Publish a private shell command; bootstrap selects dependencies at launch."""
    from hermes_cli._launchers import runtime_command

    command = directory / "hermes"
    command.write_text('#!/bin/sh\nexec ' + shlex.join(runtime_command(source)) + ' "$@"\n', encoding="utf-8")
    command.chmod(0o500)
    return directory


_CLIENT = ("from hermes_cli._startup_fast import normalize_hermes_home_env, try_management, try_session_proxy; "
           "normalize_hermes_home_env(); argv = sys.argv[1:]; try_session_proxy(argv); try_management(argv); "
           "sys.stderr.write('hermes: a managed executor reaches only its owning session\\n'); raise SystemExit(2)")


def operation_client_paths(source: Path) -> list[str]:
    """Import roots of this live owner's selected dependencies; it leases them for the task."""
    return list(dict.fromkeys([str(source), *(entry for entry in sys.path
                                              if os.path.isabs(entry) and os.path.exists(entry))]))


def write_operation_client(directory: Path, source: Path) -> Path:
    """Publish a key-free command that only forwards to the owning session.

    It reuses the owner's import paths instead of bootstrapping a dependency
    lease, so a sandboxed executor never needs write access to install state.
    """
    code = f"import sys; sys.path[:0] = {operation_client_paths(source)!r}; " + _CLIENT
    command = directory / "hermes"
    command.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, "-I", "-c", code]) + ' "$@"\n',
                       encoding="utf-8")
    command.chmod(0o500)
    return directory


def _private_root() -> Path:
    from hermes_constants import get_hermes_home

    root = get_hermes_home() / "cache" / "owned-cli"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = os.lstat(root)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise OSError(f"refusing an owned command root not privately owned: {root}")
    os.chmod(root, 0o700)
    return root


def _owner_is_dead(name: str) -> bool:
    from hermes_platform.process_identity import persistent_record_pidns_checkable
    from hermes_platform.processes import process_record_is_live

    pid, start, namespace, *_ = (name.split("-") + ["", "", ""])[:4]
    if not (pid.isdigit() and start.isdigit()):
        return False  # unknown identity is never evidence of death
    if not persistent_record_pidns_checkable(None if namespace == "x" else namespace):
        return False  # another PID namespace's owner cannot be probed here
    return not process_record_is_live({"pid": int(pid), "start_time": int(start)})


def _claim() -> Path:
    from hermes_platform.process_identity import pid_namespace_id
    from hermes_platform.processes import get_process_start_time

    root = _private_root()
    for entry in root.iterdir():
        if _owner_is_dead(entry.name):
            shutil.rmtree(entry, ignore_errors=True)
    start = get_process_start_time(os.getpid())
    prefix = f"{os.getpid()}-{start if start is not None else 'unknown'}-{pid_namespace_id() or 'x'}-"
    return Path(tempfile.mkdtemp(prefix=prefix, dir=root))


@contextlib.contextmanager
def private_cli(source: Path, *, operation_client: bool = False) -> Iterator[Path]:
    """A command directory owned by one foreground task, removed when it ends."""
    directory = _claim()
    try:
        yield (write_operation_client if operation_client else write_cli)(directory, source)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@cache
def _process_cli(source: Path) -> Path:
    directory = _claim()
    atexit.register(shutil.rmtree, directory, True)
    return write_cli(directory, source)


def owning_cli_dir(source: Path) -> Path:
    """Keep the command alive for this process, then remove it at exit."""
    return _process_cli(source.resolve())
