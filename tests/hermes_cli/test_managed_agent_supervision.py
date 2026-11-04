"""Managed foreground runners end with their owner and keep key descriptors scoped.

Native process-topology contracts for ``hermes manage run`` and
``hermes_platform.managed_supervision``: owner death and terminal cancel remove
every descendant (verified by PID and start time), including a ``setsid``
daemon that ignores SIGTERM; a trusted Hermes runner inherits a working one-shot
key FD while a vendor executor inherits nothing beyond stdio; cancelling one
supervised child leaves its sibling and the owner running.
"""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.hermes_cli.test_management_public_cli import ROOT
from tests.waiting import wait_until

pytest_plugins = ["tests.hermes_cli.test_management_public_cli", "tests.hermes_cli.test_managed_agents_public_cli"]
pytestmark = pytest.mark.live_system_guard_bypass

# A descendant that ignores polite and terminal signals. ``daemon`` double-forks
# into its own session so its original parent no longer leads to it.
_DESCENDANT = r"""
import os, signal, sys
for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    signal.signal(signum, signal.SIG_IGN)
if sys.argv[1] == "daemon":
    os.setsid()
    if os.fork():
        os._exit(0)
print(os.getpid(), flush=True)
os.dup2(os.open(os.devnull, os.O_WRONLY), 1)
while True:
    signal.pause()
"""

# Descriptors open beyond stdio, excluding the one used to list them.
_OPEN_DESCRIPTORS = """
import json, os, sys
def open_descriptors(*allowed):
    found = []
    for number in map(int, os.listdir("/dev/fd")):
        if number <= 2 or number in allowed:
            continue
        try:
            os.fstat(number)
        except OSError:
            continue
        found.append(number)
    return found
"""


def _spawn_descendants_source() -> str:
    return f"""
import subprocess, sys
def spawn_descendants():
    pids = []
    for mode in ("daemon", "worker"):
        child = subprocess.Popen([sys.executable, "-c", {_DESCENDANT!r}, mode],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
        pids.append(int(child.stdout.readline()))
    return pids
"""


def _identity(pid: int) -> tuple[int, int]:
    from hermes_platform.processes import get_process_start_time

    start = get_process_start_time(pid)
    assert start is not None, f"recorded descendant {pid} exited before it was identified"
    return pid, start


def _alive(identity: tuple[int, int]) -> bool:
    from hermes_platform.processes import get_process_start_time, pid_exists, process_start_times_agree

    pid, start = identity
    current = get_process_start_time(pid)
    return current is not None and process_start_times_agree(current, start) and pid_exists(pid)


@pytest.fixture
def identities():
    """Every recorded descendant; a red run must not leave paused processes behind."""
    from hermes_platform.processes import terminate_pid

    recorded: list[tuple[int, int]] = []
    yield recorded
    for pid, start in recorded:
        with contextlib.suppress(OSError):
            terminate_pid(pid, force=True, expected_start_time=start)


def _fake_codex(directory: Path, record: Path) -> None:
    executable = directory / "codex"
    executable.write_text(f"#!{sys.executable}\n" + _spawn_descendants_source() + f"""
import json, os, signal
from pathlib import Path
pids = [os.getpid(), *spawn_descendants()]
staging = Path({str(record)!r} + ".tmp")
staging.write_text(json.dumps(pids))
os.replace(staging, {str(record)!r})
while True:
    signal.pause()
""", encoding="utf-8")
    executable.chmod(0o700)


def _public_owner(request, directory: Path, arguments: list[str], log: Path, **kwargs) -> subprocess.Popen:
    """A real owning ``hermes`` that holds its own unlock through the fixture key FD."""
    from hermes_cli._launchers import runtime_command
    from tests.e2e.vault_home import spawn_unlocked

    world = request.getfixturevalue("world")
    env = dict(world.env, PATH=str(directory) + os.pathsep + world.env["PATH"])
    env.pop("HERMES_SESSION_VAULT", None)
    if arguments[0] != "hermes":
        # Vendor scratch must be never-swapped RAM; the fixture skips hosts without one.
        env["XDG_RUNTIME_DIR"] = str(request.getfixturevalue("ram_runtime"))
    with open(log, "wb") as output:
        return spawn_unlocked(runtime_command(ROOT, ["manage", "run", *arguments]), env=env, cwd=ROOT,
                              stdin=subprocess.DEVNULL, stdout=output, stderr=output, **kwargs)


# The exact launch seam ``hermes manage run`` uses, for hosts whose swap refuses vendor scratch.
_SEAM_OWNER = ("import os, sys; sys.path.insert(0, sys.argv[1]); "
               "from hermes_cli.managed_agents import _launch; "
               "raise SystemExit(_launch(sys.argv[2:], dict(os.environ), ()))")


def _tree_owner(request, entry: str, tmp_path: Path, record: Path, log: Path) -> subprocess.Popen:
    """Its own process group stands in for the terminal's foreground job."""
    _fake_codex(tmp_path, record)
    if entry == "public":
        return _public_owner(request, tmp_path, ["codex"], log, process_group=0)
    with open(log, "wb") as output:
        return subprocess.Popen([sys.executable, "-c", _SEAM_OWNER, str(ROOT), str(tmp_path / "codex")],
                                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                process_group=0)


def _log(log: Path) -> str:
    """Synthetic fixtures only: the fake executor prints PIDs, never secrets."""
    return log.read_text(encoding="utf-8", errors="replace")[-4000:]


def _recorded_tree(owner: subprocess.Popen, record: Path, log: Path, identities: list) -> None:
    def ready():
        if owner.poll() is not None:
            raise AssertionError(_log(log))
        with contextlib.suppress(FileNotFoundError, ValueError):
            return json.loads(record.read_text(encoding="utf-8"))
        return None

    pids = wait_until(ready, timeout=120, interval=0.05, what="the managed executor's descendants")
    identities.extend(_identity(pid) for pid in pids)


def _assert_tree_gone(identities: list, log: Path) -> None:
    try:
        wait_until(lambda: not any(map(_alive, identities)), timeout=30, interval=0.05,
                   what="every managed descendant to exit")
    except AssertionError as exc:
        raise AssertionError((str(exc), [i for i in identities if _alive(i)], _log(log))) from None


def _stop_owner(owner: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError):
        owner.kill()
    owner.wait(timeout=30)


_ENTRIES = pytest.mark.parametrize("entry", ["public", "launch-seam"])










@pytest.mark.platforms("linux", "macos")
def test_supervised_child_receives_only_the_named_descriptor():
    from hermes_platform.managed_supervision import SupervisedChild

    reader, writer = os.pipe()
    try:
        os.write(writer, b"synthetic-handoff")
    finally:
        os.close(writer)
    code = _OPEN_DESCRIPTORS + """
fd = int(sys.argv[1])
print(json.dumps({"data": os.read(fd, 64).decode(), "others": open_descriptors(fd)}))
"""
    try:
        child = SupervisedChild([sys.executable, "-c", code, str(reader)], env=dict(os.environ),
                                inherit_fds=(reader,), stdout=subprocess.PIPE)
    finally:
        os.close(reader)
    output, _ = child.process.communicate(timeout=30)
    assert child.wait(timeout=30) == 0
    assert json.loads(output) == {"data": "synthetic-handoff", "others": []}


@pytest.mark.platforms("linux")
def test_cancelling_one_supervised_child_leaves_its_sibling_and_owner_running(identities):
    from hermes_platform.managed_supervision import CANCEL_STATUS, SupervisedChild

    code = _spawn_descendants_source() + """
import os, sys
print(os.getpid(), *spawn_descendants(), flush=True)
sys.exit(0 if sys.stdin.read() == "" else 3)
"""
    owners_own = subprocess.Popen([sys.executable, "-c", _DESCENDANT, "worker"],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
    children = [SupervisedChild([sys.executable, "-c", code], env=dict(os.environ),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE) for _ in range(2)]
    try:
        identities.append(_identity(int(owners_own.stdout.readline())))
        trees = []
        for child in children:
            trees.append([_identity(int(pid)) for pid in child.process.stdout.readline().split()])
            identities.extend(trees[-1])
        cancelled, sibling = children
        assert cancelled.cancel() == CANCEL_STATUS
        wait_until(lambda: not any(map(_alive, trees[0])), timeout=30, what="the cancelled tree to exit")
        assert sibling.poll() is None and all(map(_alive, trees[1]))
        assert _alive(identities[0]), "the owner's own unsupervised child was signalled"
        sibling.process.stdin.close()
        assert sibling.wait(timeout=30) == 0
        # Normal exit still removes leftovers, including the SIGTERM-ignoring daemon.
        assert not any(map(_alive, trees[1]))
    finally:
        for child in children:
            child.cancel()
        owners_own.kill()
        owners_own.wait(timeout=30)


# Active private paths, protected vendor scratch and the sandboxed management route.


def _vendor(directory: Path, body: str) -> None:
    directory.mkdir(exist_ok=True)
    executable = directory / "codex"
    executable.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    executable.chmod(0o700)


def _run_owner(world, env) -> tuple[int, str, str]:
    from hermes_cli._launchers import runtime_command
    from tests.e2e.vault_home import spawn_unlocked

    env = dict(env)
    env.pop("HERMES_SESSION_VAULT", None)
    with spawn_unlocked(runtime_command(ROOT, ["manage", "run", "codex"]), env=env, cwd=ROOT,
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True) as process:
        stdout, stderr = process.communicate(timeout=180)
    return process.returncode, stdout, world.diagnostic(stderr, env=env)


@pytest.mark.platforms("linux", "macos")
def test_active_owned_cli_survives_an_idle_scratch_prune(tmp_path, monkeypatch):
    import tempfile
    import time

    from hermes_cli.runtime_launcher import owning_cli_dir
    from hermes_constants import export_scratch_tmp_env, get_scratch_dir, prune_scratch_dir

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    for name in ("TMPDIR", "TMP", "TEMP", "HERMES_SCRATCH_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(tempfile, "tempdir", None)
    assert export_scratch_tmp_env()  # the process state every Hermes owner boots with
    command = owning_cli_dir(ROOT) / "hermes"
    stale = time.time() - 3 * 24 * 3600
    for path in (command, command.parent):
        os.utime(path, (stale, stale), follow_symlinks=False)
    prune_scratch_dir(get_scratch_dir(prune=False), max_idle_hours=24)
    # The owning process is alive; its children still execute this command.
    assert command.is_file() and os.access(command, os.X_OK)


@pytest.mark.platforms("linux", "macos")
def test_vendor_refuses_without_proven_ram_scratch_and_never_starts(world, tmp_path):
    marker = tmp_path / "vendor-started"
    _vendor(tmp_path / "executors", f"touch '{marker}'\n")
    disk = tmp_path / "disk-runtime"
    disk.mkdir(mode=0o700)
    env = dict(world.env, PATH=str(tmp_path / "executors") + os.pathsep + world.env["PATH"],
               XDG_RUNTIME_DIR=str(disk))
    returncode, stdout, stderr = _run_owner(world, env)
    assert returncode != 0, (stdout, stderr)
    assert '"unavailable"' in stdout and "RAM" in stdout
    assert not marker.exists()




