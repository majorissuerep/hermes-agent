"""Per-session sandbox state and the effective policy for the current session.

Session overrides live in ``<HERMES_HOME>/sandbox/sessions/<sha256(session key)>.json``
(a vault envelope in a vaulted home). File-backed on purpose: the TUI runs slash
commands in a worker process, a multiplexed gateway serves many sessions, and a resumed
session should keep its grants — an in-process dict would silently desync all three.
Reads are cached by (path, mtime_ns, size), so a spawn costs one ``stat``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Callable, Optional

from hermes_security.sandbox.policy import SandboxPolicy, SessionOverrides, build_policy, sandbox_config, session_tmp_dir

_CACHE: dict[str, tuple[tuple[int, int], SessionOverrides]] = {}
_LOCK = threading.Lock()


def current_session_key() -> str:
    """The approval session key (gateway/TUI/desktop turns, their subagents, the TUI slash
    worker); a process-unique key otherwise, so two classic CLI windows never share grants."""
    from tools.approval_context import get_current_session_key
    return get_current_session_key(default="") or f"process-{os.getpid()}"


def _state_path(session_key: str) -> Path:
    from hermes_constants import get_hermes_home
    digest = hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:32]
    return Path(get_hermes_home()) / "sandbox" / "sessions" / f"{digest}.json"


def load_overrides(session_key: str) -> SessionOverrides:
    path = _state_path(session_key)
    try:
        st = path.stat()
    except FileNotFoundError:
        return SessionOverrides()
    stamp = (st.st_mtime_ns, st.st_size)
    with _LOCK:
        cached = _CACHE.get(str(path))
        if cached and cached[0] == stamp:
            return dataclasses.replace(cached[1])
    from hermes_security.io import read_json
    data = read_json(path, purpose="state") or {}
    fields = {f.name for f in dataclasses.fields(SessionOverrides)}
    overrides = SessionOverrides(**{k: v for k, v in data.items() if k in fields})
    with _LOCK:
        _CACHE[str(path)] = (stamp, overrides)
    return dataclasses.replace(overrides)


def save_overrides(session_key: str, overrides: SessionOverrides) -> None:
    from hermes_security.io import _home_for, write_json
    path = _state_path(session_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dataclasses.asdict(overrides)
    if _home_for(path) is not None:
        write_json(path, payload, purpose="state")
    else:
        tmp = path.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    with _LOCK:
        _CACHE.pop(str(path), None)


def update_overrides(session_key: str, mutate: Callable[[SessionOverrides], None]) -> SessionOverrides:
    overrides = load_overrides(session_key)
    mutate(overrides)
    save_overrides(session_key, overrides)
    return overrides


def clear_overrides(session_key: str) -> None:
    path = _state_path(session_key)
    path.unlink(missing_ok=True)
    with _LOCK:
        _CACHE.pop(str(path), None)


def session_workspace() -> str:
    """The session's working directory: the session-scoped ``TERMINAL_CWD`` (gateway,
    TUI, cron) or the process cwd (classic CLI)."""
    try:
        from gateway.session_context import get_session_env
        cwd = get_session_env("TERMINAL_CWD", "")
    except Exception:
        cwd = ""
    return cwd or os.environ.get("TERMINAL_CWD") or os.getcwd()


def effective_policy(session_key: Optional[str] = None, *, config: Optional[dict] = None,
                     with_tmp: bool = True) -> SandboxPolicy:
    key = session_key or current_session_key()
    cfg = sandbox_config() if config is None else config
    return build_policy(cfg, load_overrides(key), workspace=session_workspace(),
                        tmp_dir=session_tmp_dir(key) if with_tmp and cfg.get("enabled") else "")
