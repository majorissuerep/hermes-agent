"""Foreground native children whose whole process tree ends with their owner.

The owner starts a small private supervisor (``python -I``) which starts the real
child with the owner's stdio and, by default, process group, so terminal job control and
Ctrl-C reach it unchanged. Explicit resident consumers may isolate the supervisor and child
in a new session; their control EOF/cancel still owns the same supervised tree. A control pipe, held only by the owner, carries one
JSON spec line and then an optional ``cancel`` line; its EOF means the owner died.

Linux: the supervisor is a child subreaper, so every orphaned descendant,
including ``setsid`` daemons, reparents to it. Cleanup signals only processes
provably descended from it, each pinned by PID, start time and a pidfd:
SIGTERM, a bounded grace, then freeze-and-SIGKILL rounds until none remain.

macOS has neither subreapers nor pidfds. The supervisor periodically remembers
psutil descendants by PID and creation time and signals the child's own process
group while that child is unreaped. A descendant that is orphaned to launchd
before a snapshot sees it can escape; this is best effort, not containment.
"""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

CANCEL = b"cancel\n"
CANCEL_STATUS = 128 + signal.SIGINT
TERMINATE_GRACE = 2.0
KILL_DEADLINE = 10.0
_REMEMBER_INTERVAL = 0.25
_ROOT = str(Path(__file__).resolve().parents[1])
_BOOTSTRAP = ("import sys; sys.path.insert(0, sys.argv[1]); "
              "from hermes_platform.managed_supervision import _supervise_main; "
              "raise SystemExit(_supervise_main(int(sys.argv[2])))")


def exit_status(returncode: int) -> int:
    """Shell convention: death by signal N reports 128+N."""
    return returncode if returncode >= 0 else 128 - returncode


class SupervisedChild:
    """One native child under its own supervisor; siblings and the owner are unaffected.

    The caller keeps ownership of ``inherit_fds`` and closes them once construction
    returns or raises; the supervisor closes its copies after starting the child.
    The control pipe never reaches the child.
    """

    def __init__(self, argv, *, env, cwd=None, inherit_fds=(), stdin=None, stdout=None, stderr=None,
                 cleanup_dirs=(), start_new_session=False):
        from hermes_platform.owned_cleanup import capture_directories, close_directories

        fds = tuple(int(fd) for fd in inherit_fds)
        cleanup = capture_directories(cleanup_dirs)
        try:
            spec = json.dumps({"argv": [os.fspath(arg) for arg in argv], "fds": fds,
                               "cleanup": cleanup, "path": [entry for entry in sys.path if isinstance(entry, str)]})
            reader, self._control = os.pipe()
            try:
                self.process = subprocess.Popen(  # health: allow HX006 -- the supervisor owns the native child's lifetime
                    [sys.executable, "-I", "-c", _BOOTSTRAP, _ROOT, str(reader)], env=env, cwd=cwd,
                    pass_fds=(reader, *fds, *(entry["parent"] for entry in cleanup)),
                    stdin=stdin, stdout=stdout, stderr=stderr, start_new_session=start_new_session)
            except BaseException:
                self._close_control()
                raise
            finally:
                os.close(reader)
            self._send(spec.encode("utf-8") + b"\n")
        finally:
            close_directories(cleanup)

    @property
    def pid(self) -> int:
        return self.process.pid

    def _send(self, data: bytes) -> None:
        view = memoryview(data)
        while view and self._control is not None:
            try:
                view = view[os.write(self._control, view):]
            except BrokenPipeError:
                return  # The supervisor already exited; wait() reports its status.

    def _close_control(self) -> None:
        if self._control is not None:
            os.close(self._control)
            self._control = None

    def poll(self) -> int | None:
        returncode = self.process.poll()
        if returncode is None:
            return None
        self._close_control()
        return exit_status(returncode)

    def wait(self, timeout: float | None = None) -> int:
        returncode = self.process.wait(timeout)
        self._close_control()
        return exit_status(returncode)

    def cancel(self, timeout: float = TERMINATE_GRACE + KILL_DEADLINE + 5) -> int | None:
        """Request tree cleanup; ``None`` when the supervisor is still cleaning at the deadline."""
        self._send(CANCEL)
        self._close_control()  # EOF is also a cancel if the line was not read.
        try:
            return self.wait(timeout)
        except subprocess.TimeoutExpired:
            return None


def run_foreground(argv, *, env, cwd=None, inherit_fds=(), cleanup_dirs=()) -> int:
    """Run a terminal-owned child; Ctrl-C or owner exit cleans its whole tree."""
    child = SupervisedChild(argv, env=env, cwd=cwd, inherit_fds=inherit_fds, cleanup_dirs=cleanup_dirs)
    try:
        return child.wait()
    except KeyboardInterrupt:
        child.cancel()
        return CANCEL_STATUS
    except BaseException:
        child.cancel()
        raise


# Supervisor process ---------------------------------------------------------


def _ignore(_signum, _frame) -> None:
    """A caught (not ignored) disposition resets to default in the exec'd child."""


class _Target:
    """A descendant pinned by PID and start time; never signalled after reuse."""

    def __init__(self, pid: int, start: int, handle=None) -> None:
        self.pid, self.start, self.handle = pid, start, handle

    def send(self, signum: int) -> None:
        if self.handle is not None:  # psutil refuses a reused PID before signalling.
            with contextlib.suppress(Exception):
                self.handle.send_signal(signum)
            return
        from hermes_platform.processes import get_process_start_time, process_start_times_agree
        try:
            descriptor = os.pidfd_open(self.pid)
        except OSError:
            return
        try:
            # The pidfd pins one process; matching its start time proves it is ours.
            current = get_process_start_time(self.pid)
            if current is not None and process_start_times_agree(current, self.start):
                signal.pidfd_send_signal(descriptor, signum)
        except ProcessLookupError:
            return
        finally:
            os.close(descriptor)


def _linux_parents() -> dict[int, int]:
    """Live (non-zombie) PID → parent PID from procfs."""
    parents = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as handle:
                fields = handle.read().rsplit(b")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if fields and fields[0] != b"Z":
            parents[int(name)] = int(fields[1])
    return parents


def _linux_descendants(root: int) -> dict[tuple[int, int], _Target]:
    from hermes_platform.processes import get_process_start_time
    children: dict[int, list[int]] = {}
    for pid, parent in _linux_parents().items():
        children.setdefault(parent, []).append(pid)
    found, pending = {}, list(children.get(root, ()))
    while pending:
        pid = pending.pop()
        start = get_process_start_time(pid)
        if start is not None:
            found[(pid, start)] = _Target(pid, start)
        pending.extend(children.get(pid, ()))
    return found


def _psutil_descendants(root: int) -> dict[tuple[int, int], _Target]:
    import psutil
    found = {}
    try:
        processes = psutil.Process(root).children(recursive=True)
    except psutil.Error:
        return found
    for process in processes:
        with contextlib.suppress(psutil.Error):
            if process.status() != psutil.STATUS_ZOMBIE:
                start = round(process.create_time() * 100)
                found[(process.pid, start)] = _Target(process.pid, start, process)
    return found


def _set_subreaper() -> bool:
    if not sys.platform.startswith("linux") or not hasattr(os, "pidfd_open"):
        return False
    import ctypes
    return ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0  # PR_SET_CHILD_SUBREAPER


class _Supervisor:
    def __init__(self, control: int, fds: tuple[int, ...]) -> None:
        self.control, self.fds = control, fds
        self.buffer = b""
        self.child: subprocess.Popen | None = None
        self.child_status: int | None = None
        self.stop_status: int | None = None
        self.known: dict[tuple[int, int], _Target] = {}
        self.contained = _set_subreaper()
        self.wakeup, writer = os.pipe()
        for descriptor in (self.wakeup, writer):
            os.set_blocking(descriptor, False)
        signal.set_wakeup_fd(writer)
        for signum in (signal.SIGINT, signal.SIGQUIT):
            if signal.getsignal(signum) is not signal.SIG_IGN:
                signal.signal(signum, _ignore)  # The terminal's own signal reaches the child.
        for signum in (signal.SIGCHLD, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, self._on_signal)

    def _on_signal(self, signum, _frame) -> None:
        if signum != signal.SIGCHLD and self.stop_status is None:
            self.stop_status = 128 + signum

    def read_line(self) -> bytes | None:
        while b"\n" not in self.buffer:
            try:
                chunk = os.read(self.control, 65536)
            except InterruptedError:
                continue
            if not chunk:
                return None
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        return line

    def spawn(self, argv: list[str]) -> int | None:
        try:
            self.child = subprocess.Popen(argv, pass_fds=self.fds)  # health: allow HX006 -- reaped and tree-killed by this supervisor
        except OSError as exc:
            print(f"hermes: managed child could not start: {exc.strerror or type(exc).__name__}", file=sys.stderr)
            return 127 if isinstance(exc, FileNotFoundError) else 126
        finally:
            for fd in self.fds:
                with contextlib.suppress(OSError):
                    os.close(fd)
        return None

    def reap(self) -> None:
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if pid == 0:
                return
            if self.child is not None and pid == self.child.pid:
                self.child.returncode = os.waitstatus_to_exitcode(status)
                self.child_status = exit_status(self.child.returncode)

    def _read_control(self) -> None:
        try:
            chunk = os.read(self.control, 4096)
        except BlockingIOError:
            return
        if not chunk:
            self.stop_status = CANCEL_STATUS  # Owner death: nobody waits for this status.
            return
        self.buffer += chunk
        if CANCEL.strip() in self.buffer:
            self.stop_status = CANCEL_STATUS

    def pause(self, timeout: float, *, control: bool = False) -> None:
        """Wait for a signal (including SIGCHLD) or owner input, then reap."""
        poller = select.poll()
        poller.register(self.wakeup, select.POLLIN)
        if control:
            poller.register(self.control, select.POLLIN | select.POLLHUP)
        for descriptor, _events in poller.poll(timeout * 1000):
            if descriptor == self.wakeup:
                with contextlib.suppress(BlockingIOError):
                    while os.read(self.wakeup, 4096):
                        pass
            else:
                self._read_control()
        self.reap()

    def supervise(self) -> None:
        if CANCEL.strip() in self.buffer:
            self.stop_status = CANCEL_STATUS
        self.reap()
        while self.child_status is None and self.stop_status is None:
            if not self.contained:
                self.known.update(self.descendants())
            self.pause(_REMEMBER_INTERVAL if not self.contained else 5.0, control=True)

    def descendants(self) -> dict[tuple[int, int], _Target]:
        if self.contained:
            return _linux_descendants(os.getpid())
        current = _psutil_descendants(os.getpid())
        self.known = {key: target for key, target in self.known.items() if target.handle.is_running()}
        return {**self.known, **current}

    def _signal_group(self, signum: int) -> None:
        """macOS fallback: an unreaped child still pins its own process group ID."""
        if self.contained or self.child is None or self.child.returncode is not None:
            return
        with contextlib.suppress(OSError):
            if os.getpgid(self.child.pid) == self.child.pid:
                os.killpg(self.child.pid, signum)

    def _freeze(self) -> dict[tuple[int, int], _Target]:
        """Stop descendants until a rescan finds no new fork."""
        stopped: dict[tuple[int, int], _Target] = {}
        for _ in range(64):
            fresh = {key: target for key, target in self.descendants().items() if key not in stopped}
            if not fresh:
                break
            for key, target in fresh.items():
                target.send(signal.SIGSTOP)
                stopped[key] = target
        return stopped

    def kill_tree(self) -> None:
        targets = self.descendants()
        if not targets:
            return
        self._signal_group(signal.SIGTERM)
        for target in targets.values():
            target.send(signal.SIGTERM)
            target.send(signal.SIGCONT)  # A job-stopped process cannot act on SIGTERM.
        deadline = time.monotonic() + TERMINATE_GRACE
        while self.descendants() and time.monotonic() < deadline:
            self.pause(0.05)
        deadline = time.monotonic() + KILL_DEADLINE
        while self.descendants() and time.monotonic() < deadline:
            self._signal_group(signal.SIGKILL)
            for target in self._freeze().values():
                target.send(signal.SIGKILL)
            self.pause(0.05)


def _supervise_main(control: int) -> int:
    """Entry point of the private supervisor process."""
    os.set_inheritable(control, False)
    supervisor = _Supervisor(control, ())
    line = supervisor.read_line()
    if line is None or supervisor.stop_status is not None:
        return supervisor.stop_status or CANCEL_STATUS  # Stopped before the child existed.
    spec = json.loads(line)
    sys.path.extend(entry for entry in spec["path"] if entry not in sys.path)
    supervisor.fds = tuple(spec["fds"])
    os.set_blocking(control, False)
    from hermes_platform.owned_cleanup import close_directories, remove_directories

    cleanup = spec.get("cleanup", [])
    try:
        failure = supervisor.spawn(spec["argv"])
        if failure is not None:
            return failure
        supervisor.supervise()
        return supervisor.child_status if supervisor.stop_status is None else supervisor.stop_status
    finally:
        supervisor.kill_tree()
        supervisor.reap()
        if supervisor.descendants():
            close_directories(cleanup)
            raise RuntimeError("Managed descendants survived cleanup; scratch retained")
        remove_directories(cleanup)
