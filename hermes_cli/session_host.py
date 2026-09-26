"""The session host: ONE machine-level ``hermes serve`` that owns deck sessions, plus a small sync client.

Terminals are clients of the host, so closing a terminal detaches its session instead of killing it, and
any process (another terminal, ``hermes deck``, an agent's ``sessions`` tool) reaches every open session
through the same JSON-RPC surface. Discovery rides the existing host rendezvous record
(``gateway/host_rendezvous.py``: pid + create-time + 0600 token); a missing host is spawned detached
(new session, no TTY) with the launcher's unlocked vault handed over on an inherited pipe fd
(``hermes_security/handoff.py``) — never a password in the environment.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional

_SPAWN_TIMEOUT_S = 45.0
_POLL_S = 0.3


class HostUnavailable(RuntimeError):
    """No session host answers and one could not be started."""


class HostRpcError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def host_ws_url() -> Optional[str]:
    """``ws://…/api/ws?token=…`` of the live host, or None. The owner must PROVE itself (TCP + an
    identity answer authenticated with the token), not merely have a record on disk."""
    from gateway import host_rendezvous as hr

    record = hr.read_record(hr.ROLE_SERVE)
    if record is None or not record.port or not hr.record_token_is_consistent(record):
        return None
    if hr.probe_owner(record) is None:
        return None
    token = hr.read_token(hr.ROLE_SERVE)
    host = hr.dial_host(record)
    netloc = f"[{host}]:{record.port}" if ":" in host else f"{host}:{record.port}"
    return f"ws://{netloc}/api/ws?{urllib.parse.urlencode({'token': token})}"


def _ensure_root_vault_unlocked(root: Path) -> None:
    """The host runs at the machine root; a launcher scoped to a named profile may not hold the root's
    key yet. Unlock it here, on the launcher's terminal, because the detached host has none."""
    from hermes_cli.vault_gate import vault_exists_light

    if not vault_exists_light(root):
        return
    from hermes_security import vault as vault_mod

    if vault_mod.is_unlocked(root):
        return
    from hermes_cli.vault_cmd import _password_from_env_or_prompt

    vault_mod.unlock(root, _password_from_env_or_prompt(confirm="Unlock the session host (machine vault): "))


def _spawn_host() -> subprocess.Popen:
    from hermes_constants import get_default_hermes_root
    from tools.environments.local import build_subprocess_env

    root = Path(get_default_hermes_root()).resolve()
    _ensure_root_vault_unlocked(root)
    # Pinned to the machine root with -p default, like the named-profile dashboard reroute: the sticky
    # active_profile file must not re-scope the machine host to one profile.
    # Port 0: clients find the host through the rendezvous record, so a fixed port would only collide.
    argv = [sys.executable, "-m", "hermes_cli.main", "-p", "default", "serve", "--port", "0"]
    env = build_subprocess_env(scrub_secrets=False, inherit_profile_home=False)
    env["HERMES_HOME"] = str(root)
    kwargs: Dict[str, Any] = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, cwd=str(root), env=env, close_fds=True)
    handoff = None
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
        from hermes_security.handoff import prepare_child_key

        with contextlib.suppress(ImportError):
            handoff = prepare_child_key()
        if handoff is not None:
            env.update(handoff[1])
            kwargs["pass_fds"] = (handoff[0],)
    try:
        return subprocess.Popen(argv, **kwargs)  # noqa: S603 — our own interpreter + module
    finally:
        if handoff is not None:
            os.close(handoff[0])


def ensure_host(*, timeout_s: float = _SPAWN_TIMEOUT_S) -> str:
    """The live host's WS URL, starting the host if none answers."""
    if url := host_ws_url():
        return url
    from hermes_cli.session_host_diagnostics import write_startup_receipt

    started = time.monotonic()
    child = _spawn_host()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if url := host_ws_url():
            write_startup_receipt(status="ready", pid=child.pid, started=started)
            return url
        if (exit_code := child.poll()) is not None:
            receipt = write_startup_receipt(status="exited", pid=child.pid, started=started,
                                            exit_code=exit_code)
            raise HostUnavailable(
                f"the session host exited with exit code {exit_code}; "
                f"startup receipt: {receipt or 'unavailable'} — inspect `hermes logs errors` "
                "or run `hermes serve` in a terminal")
        time.sleep(min(_POLL_S, max(0, deadline - time.monotonic())))
    receipt = write_startup_receipt(status="timeout", pid=child.pid, started=started)
    raise HostUnavailable(
        f"the session host did not come up within {timeout_s:.0f}s; "
        f"startup receipt: {receipt or 'unavailable'} — run `hermes serve` in a terminal to see why")


class HostClient:
    """Blocking JSON-RPC over the host's WebSocket. Event frames and server→client requests are ignored:
    deck verbs are plain request/response."""

    def __init__(self, url: str, *, open_timeout: float = 10.0):
        from websockets.sync.client import connect

        self._ws = connect(url, open_timeout=open_timeout, max_size=None, proxy=None)
        self._ids = itertools.count(1)

    def call(self, method: str, params: Optional[Dict[str, Any]] = None, *, timeout: float = 60.0) -> Dict[str, Any]:
        rid = f"deck-{next(self._ids)}"
        self._ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{method}: no answer from the session host within {timeout:.0f}s")
            frame = json.loads(self._ws.recv(timeout=remaining))
            if frame.get("id") != rid or "method" in frame:
                continue
            if "error" in frame:
                error = frame["error"] or {}
                raise HostRpcError(int(error.get("code") or -1), str(error.get("message") or "error"))
            return frame.get("result") or {}

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._ws.close()

    def __enter__(self) -> "HostClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def connect(*, start: bool = True) -> HostClient:
    url = ensure_host() if start else host_ws_url()
    if not url:
        raise HostUnavailable("no session host is running")
    return HostClient(url)
