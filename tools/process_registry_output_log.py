"""File-backed output for background processes a registered worker starts.

A managed worker is a per-turn interpreter. A stdout pipe whose read end that worker's reader
thread owns closes when the worker exits, so the child's next write raises SIGPIPE
(``broken_pipe``) and everything it printed is gone. Under a worker the child writes to a
profile-scoped encrypted stream through a lifetime-owned sink which records its exit status,
so whichever interpreter adopts the checkpoint entry next
(the session's next worker, the owner at startup) tails the same file and collects the exit.
"""
import codecs
from contextlib import suppress
import logging
import os
from pathlib import Path
import select
import signal
import subprocess
import struct
import sys
import time
from typing import Optional

from hermes_constants import get_hermes_home
from hermes_security.errors import VaultError, VaultIntegrityError

logger = logging.getLogger("tools.process_registry")

_TAIL_SECONDS = 0.2
# After the process ended: an orphaned grandchild may keep appending; stop draining after this.
_FINAL_DRAIN_MAX_BYTES = 1 << 20


def output_dir() -> Path:
    return get_hermes_home() / "logs" / "process-output"


def background_output_log(session_id: str) -> Optional[Path]:
    """Encrypted stream for a durable native process, retained across launcher exit."""
    directory = output_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    from tools.process_registry_results import RESULT_RETENTION_SECONDS
    # Only a recorded exit retires a log: a quiet process can run longer than the retention.
    cutoff = time.time() - RESULT_RETENTION_SECONDS
    for exited in directory.glob("proc_*.exit"):
        with suppress(FileNotFoundError):
            if exited.stat().st_mtime < cutoff:
                exited.with_suffix(".log").unlink(missing_ok=True)
                exited.unlink()
    return directory / f"{session_id}.log"


def adoptable_log(path) -> str:
    """A checkpointed log path, only when it names this profile's output directory."""
    log = Path(str(path or ""))
    return str(log) if path and log.parent == output_dir() and log.suffix == ".log" else ""


def spawn_output_worker(log: Path, argv: list[str], *, env: dict, execution_id: str, **kwargs):
    """Return only after the lifetime-owned sink adopted its key and started the child."""
    from tools.process_registry import _IS_WINDOWS
    from hermes_security.handoff import prepare_child_key
    from hermes_platform.child_processes import spawn_env
    from hermes_platform.processes import get_process_start_time

    if _IS_WINDOWS:
        raise RuntimeError("Durable encrypted background processes require Linux or macOS")
    handoff = prepare_child_key()
    if handoff is None:
        raise RuntimeError("Durable process output requires an unlocked profile vault")
    key_fd, key_env = handoff
    ready_read, ready_write = os.pipe()
    child_env = {**env, **key_env, **spawn_env("process-output"), "HERMES_HOME": str(get_hermes_home())}
    worker = Path(__file__).with_name("process_output_worker.py")
    proc = None
    try:
        proc = subprocess.Popen(
            [sys.executable, str(worker), str(log), str(ready_write), execution_id, *argv],
            env=child_env, pass_fds=(key_fd, ready_write), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, **kwargs)
        started_at = get_process_start_time(proc.pid)
        os.close(ready_write)
        ready_write = -1
        readable, _, _ = select.select([ready_read], [], [], 15)
        if not readable or os.read(ready_read, 1) != b"R":
            raise RuntimeError("Durable process output worker failed to adopt vault authority or start")
        return proc, started_at
    except BaseException:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        raise
    finally:
        os.close(key_fd)
        os.close(ready_read)
        if ready_write >= 0:
            os.close(ready_write)


def _exit_path(log) -> Path:
    return Path(log).with_suffix(".exit")


def recorded_exit_code(log) -> Optional[int]:
    from hermes_security import io as state_io

    try:
        code = state_io.read_json(_exit_path(log), purpose="state")
        return None if code is None else int(code)
    except (OSError, ValueError, TypeError):
        return None


class ProcessOutputLogMixin:
    """The subclass supplies ``_ingest_output``, ``_clean_shell_noise``, ``_finish_reader``,
    ``_finish_exited`` and ``_detached_host_fate``. See ProcessRegistry."""

    def _log_reader_loop(self, session) -> None:
        """Tail ``session.output_log`` until its process ends. The spawner decides the exit from
        its Popen; an adopter (no Popen) from the recorded status, else ``lost`` (-1)."""
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        proc = session.process
        # poll()/wait() reconcile asks this reader to finish instead of reading a pipe itself.
        session._reader_selectable = True

        head_noise = True

        def ingest(text: str) -> None:
            nonlocal head_noise
            if head_noise:  # ``bash -lic`` startup warnings, as the pipe reader strips them
                text = self._clean_shell_noise(text)
                head_noise = not text.strip()
            self._ingest_output(session, text)

        def running() -> bool:
            if session._reader_finish_requested.is_set():
                return False
            if proc is not None:
                return proc.poll() is None
            return self._detached_host_fate(session.pid, session.host_start_time) == "running"
        try:
            from hermes_security import frames
            from hermes_security.vault import get_vault
            vault = get_vault()
            with open(session.output_log, "rb") as stream:
                drained = 0
                pending = b""
                while True:
                    # Decided BEFORE the read: once the process is gone every byte it wrote is readable.
                    alive = running()
                    chunk = stream.read(65536)
                    if chunk:
                        pending += chunk
                        payloads, consumed = frames.split_stream(pending, vault=vault, purpose="log", strict=True)
                        pending = pending[consumed:]
                        if (len(pending) >= 4 and struct.unpack_from(">I", pending)[0] > 1 << 20):
                            raise VaultIntegrityError("Process output frame exceeds the stream limit")
                        for payload in payloads:
                            if text := decoder.decode(payload):
                                ingest(text)
                        drained += 0 if alive else len(chunk)
                        if drained < _FINAL_DRAIN_MAX_BYTES:
                            continue
                    if not alive:
                        if pending:
                            raise VaultIntegrityError("Incomplete durable process output frame")
                        break
                    time.sleep(_TAIL_SECONDS)
        except VaultError:
            self._terminate_host_pid(session.pid, session.host_start_time)
            self._finish_exited(session, -1, reason="lost", source="backend_lost")
            logger.error("Process output log failed vault authentication: %s", session.output_log)
            return
        except OSError as exc:
            logger.warning("Process output log %s unreadable: %s", session.output_log, exc)
        if proc is not None:
            self._finish_reader(session, decoder, ingest, "Log", proc.wait, lambda: proc.returncode)
            return
        if tail := decoder.decode(b"", final=True):
            ingest(tail)
        try:
            code = recorded_exit_code(session.output_log)
        except VaultError:
            self._finish_exited(session, -1, reason="lost", source="backend_lost")
            logger.error("Process exit record failed vault authentication: %s", session.output_log)
            return
        if code is None:
            # SIGKILL / exec past the trap: the spawner is gone and no status was recorded.
            self._finish_exited(session, -1, reason="lost", source="backend_lost")
        else:
            self._finish_exited(session, code)
