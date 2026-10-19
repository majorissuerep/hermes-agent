"""Scoped vault adoption for workers that can outlive a finite dispatcher."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import time


def execution_session(env: dict[str, str]) -> str:
    return f"kanban:{env['HERMES_KANBAN_TASK']}:{env['HERMES_KANBAN_RUN_ID']}"


def _ready_path(env: dict[str, str]) -> Path:
    identity = hashlib.sha256(execution_session(env).encode()).hexdigest()
    return Path(env["HERMES_HOME"]) / "kanban/worker-adoptions" / f"{identity}.json"


def _prepare(env: dict[str, str]):
    from hermes_security.session_authority import ENV_NAME, start_session_authority
    from hermes_security.errors import VaultLockedError
    from hermes_security.vault import vault_exists

    home = Path(env["HERMES_HOME"]).resolve()
    has_vault = any(vault_exists(candidate) for candidate in (home, *home.parents))
    if raw := env.get(ENV_NAME):
        from agent.session_context import get_session_env
        env.setdefault("HERMES_SESSION_ID", get_session_env("HERMES_SESSION_ID") or "")
        grant = json.loads(raw)
        if grant.get("home") != str(home) or grant.get("session_id") != env.get("HERMES_SESSION_ID"):
            raise RuntimeError("Kanban worker grant does not authorize its profile/session")
        # A live terminal's grant must remain revocable by that terminal.
        return None
    if not has_vault:
        return None  # Explicit pre-vault test/embedded callers do not need a key handoff.
    session_id = execution_session(env)
    authority = start_session_authority(home, session_id)
    if authority is None:
        raise VaultLockedError("Kanban dispatcher must unlock the worker's owning profile")
    env["HERMES_SESSION_ID"] = session_id
    env.update(authority.environment())
    return authority


def _launched_worker(process, fingerprint: str, receipt: dict) -> bool:
    """Bind adoption to this exact child or its still-owned scope launcher."""
    import psutil
    from hermes_cli.kanban_db_dispatch import _worker_alive

    if not _worker_alive(process.pid, fingerprint):
        return False
    if receipt.get("pid") == process.pid:
        return receipt.get("started_at") == fingerprint
    try:
        descendants = {child.pid for child in psutil.Process(process.pid).children(recursive=True)}
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    return (receipt.get("pid") in descendants
            and _worker_alive(receipt.get("pid"), receipt.get("started_at"))
            and _worker_alive(process.pid, fingerprint))


def spawn_worker(argv: list[str], *, env: dict[str, str], **kwargs) -> subprocess.Popen:
    """Keep a finite launcher's scoped grant until the child owns its key."""
    from cron.executions import HANDOFF_ADOPTION_GRACE_SECONDS
    from hermes_security.io import read_json
    from hermes_cli.kanban_db_dispatch import (
        _process_fingerprint, _terminate_reclaimed_worker, UNVERIFIED_WORKER_FINGERPRINT,
    )

    env = dict(env)
    authority = _prepare(env)
    ready = _ready_path(env) if authority is not None else None
    process = None
    fingerprint = UNVERIFIED_WORKER_FINGERPRINT
    try:
        if ready is not None:
            ready.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            ready.unlink(missing_ok=True)
        process = subprocess.Popen(argv, env=env, **kwargs)
        fingerprint = _process_fingerprint(process.pid) or UNVERIFIED_WORKER_FINGERPRINT
        if ready is not None:
            deadline = time.monotonic() + HANDOFF_ADOPTION_GRACE_SECONDS
            while time.monotonic() < deadline:
                if ready.is_file():
                    receipt = read_json(ready, purpose="state")
                    if (receipt.get("claim") == env["HERMES_KANBAN_CLAIM_LOCK"]
                            and receipt.get("run_id") == int(env["HERMES_KANBAN_RUN_ID"])
                            and _launched_worker(process, fingerprint, receipt)):
                        return process
                    raise RuntimeError("Kanban worker adoption does not match its claim")
                if process.poll() is not None:
                    # Preserve the real worker's exit/log for normal crash accounting.
                    return process
                time.sleep(0.05)
            raise RuntimeError("Kanban worker did not adopt its vault authority before the startup deadline")
        return process
    except BaseException:
        if process is not None:
            _terminate_reclaimed_worker(process.pid, env["HERMES_KANBAN_CLAIM_LOCK"], started_at=fingerprint)
            process.poll()
        raise
    finally:
        if authority is not None:
            authority.close()
        if ready is not None:
            ready.unlink(missing_ok=True)


def adopt_worker_execution():
    """Publish adoption before touching the dispatcher's still-committing board."""
    from hermes_constants import get_hermes_home
    from hermes_security.session_authority import start_session_authority
    from hermes_security.io import write_json
    from hermes_cli.kanban_db_dispatch import _process_fingerprint

    if os.environ.get("HERMES_SESSION_ID") != execution_session(os.environ):
        return None
    authority = start_session_authority(get_hermes_home(), os.environ["HERMES_SESSION_ID"])
    if authority is None:
        raise RuntimeError("Kanban worker has no adopted vault authority")
    os.environ.update(authority.environment())
    ready = _ready_path(os.environ)
    try:
        write_json(ready, {"claim": os.environ["HERMES_KANBAN_CLAIM_LOCK"], "pid": os.getpid(),
                          "run_id": int(os.environ["HERMES_KANBAN_RUN_ID"]),
                          "started_at": _process_fingerprint(os.getpid())}, purpose="state")
    except BaseException:
        authority.close()
        raise
    return authority
