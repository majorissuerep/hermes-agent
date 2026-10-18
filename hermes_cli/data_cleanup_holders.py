"""Home-scoped runtime coordination for an explicitly confirmed data wipe."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from pathlib import Path
import time


def _refuse_cron_writers(home: Path) -> None:
    from contextlib import closing
    import sqlite3
    from hermes_platform.processes import get_process_start_time, pid_exists

    path = home / "cron" / "executions.db"
    try:
        path.stat()
    except FileNotFoundError:
        return
    # Importing cron initializes scheduler/config defaults. A deletion probe
    # must only read the existing ledger, not recreate the data being removed.
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM executions WHERE status IN ('claimed','running')").fetchall()
    except sqlite3.Error as exc:
        raise RuntimeError(f"cannot inspect cron owners in {path}: {exc}") from exc
    for raw in rows:
        row = dict(raw)
        pid = row["pid"]
        if row.get("handoff_pending"):
            raise RuntimeError(f"cron execution {row['id']} is being handed to a worker; wait for it to finish")
        if not pid_exists(pid):
            continue
        current = get_process_start_time(pid)
        recorded = row.get("process_started_at")
        if current is None or recorded is None or current == recorded:
            raise RuntimeError(f"cron execution {row['id']} still owns {home} (PID {pid}); stop it before retrying")




@contextmanager
def quiescent_home(home: Path):
    """Hold existing writer locks through deletion; do not remove their inodes."""
    from hermes_state.runtime_ownership import exclusive_maintenance
    from hermes_cli.active_sessions import _FileLock, _prune_dead, _read_entries
    from hermes_cli.runtime_state import _lock
    from tools.checkpoint_pruning import store_lock

    home = home.resolve()
    with ExitStack() as stack:
        stack.enter_context(store_lock(home / "checkpoints"))
        stack.enter_context(exclusive_maintenance([home]))
        stack.enter_context(_FileLock(home / "runtime" / "active_sessions.lock"))
        sessions = _prune_dead(_read_entries(home / "runtime" / "active_sessions.json", strict=True), strict=True)
        if sessions:
            pids = sorted({entry["pid"] for entry in sessions})
            raise RuntimeError(f"live chat sessions still own {home} (PIDs {pids}); close them before retrying")
        for path in (home / ".backup.lock", home / "cron" / ".tick.lock"):
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = stack.enter_context(path.open("a+b"))
            if not _lock(handle.fileno(), wait=False):
                raise RuntimeError(f"an active writer still owns {path}")
        _refuse_cron_writers(home)
        yield
