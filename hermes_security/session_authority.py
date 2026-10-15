"""An unlocked session lends its own vault authority to local Hermes commands.

The passphrase is never retained. Children redeem an ephemeral loopback capability
for the already-unlocked key, and verify it through the normal vault handoff.
Closing the owning agent revokes the capability. Nothing is written to disk.
"""
from __future__ import annotations

import atexit
import base64
import json
import os
from pathlib import Path
import secrets
import socket
import socketserver
import threading

ENV_NAME = "HERMES_SESSION_VAULT"
_LIMIT = 8192
_lock = threading.RLock()
_sessions: dict[tuple[str, str], SessionAuthority] = {}


class SessionAuthorityError(RuntimeError):
    """A supplied session grant is invalid, revoked, or belongs to another home."""


def _vault_home(home: Path) -> Path | None:
    return next((p for p in (home, *home.parents) if (p / ".hermes-vault").is_file()), None)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(5)
        line = self.rfile.readline(_LIMIT + 1)
        if not line.endswith(b"\n") or len(line) > _LIMIT:
            return
        try:
            request = json.loads(line)
        except (ValueError, UnicodeError):
            return
        authority = self.server.authority
        with _lock:
            registered = _sessions.get((str(authority.home), authority.session_id)) is authority
        token = request.get("token") if isinstance(request, dict) else None
        if (not registered or not isinstance(token, str) or not secrets.compare_digest(token, authority.token)
                or request.get("home") != str(authority.home)
                or request.get("session_id") != authority.session_id):
            response = {"error": "session unlock does not authorize this home"}
        else:
            from hermes_security.vault import export_unlocked_key
            from hermes_security.errors import VaultLockedError

            try:
                key = export_unlocked_key(authority.vault_home)
            except VaultLockedError:
                response = {"error": "the owning session is locked"}
            else:
                response = {"home": str(authority.vault_home), "key": base64.b64encode(key).decode("ascii")}
        self.wfile.write(json.dumps(response).encode("utf-8") + b"\n")


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    block_on_close = False


class SessionAuthority:
    def __init__(self, home: Path, session_id: str, vault_home: Path) -> None:
        self.home, self.session_id, self.vault_home = home, session_id, vault_home
        self.token = secrets.token_urlsafe(32)
        from agent.redact import register_vault_redaction_value
        register_vault_redaction_value(self.token)
        self.references = 1
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.server.authority = self
        from agent.memory_provider import spawn_context_thread
        self.thread = spawn_context_thread(self.server.serve_forever, kwargs={"poll_interval": 0.05},
                                           name="hermes-session-unlock", daemon=True)
        self.thread.start()

    def environment(self) -> dict[str, str]:
        return {ENV_NAME: json.dumps({"port": self.server.server_address[1], "token": self.token,
                                      "home": str(self.home), "session_id": self.session_id})}

    def close(self) -> None:
        with _lock:
            identity = (str(self.home), self.session_id)
            if _sessions.get(identity) is not self:
                return
            self.references -= 1
            if self.references:
                return
            del _sessions[identity]
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def start_session_authority(home: Path | str, session_id: str) -> SessionAuthority | None:
    """Register only an already-unlocked home; never prompt or unlock another profile."""
    from hermes_security.vault import is_unlocked

    home = Path(home).resolve()
    owner = _vault_home(home)
    if owner is None or not session_id or not is_unlocked(owner):
        return None
    identity = (str(home), session_id)
    with _lock:
        if existing := _sessions.get(identity):
            existing.references += 1
            return existing
        authority = SessionAuthority(home, session_id, owner)
        _sessions[identity] = authority
        return authority


def session_environment(home: Path | str, session_id: str) -> dict[str, str]:
    with _lock:
        authority = _sessions.get((str(Path(home).resolve()), session_id))
        return authority.environment() if authority else {}


def apply_session_environment(env: dict[str, str]) -> None:
    """Select the current session, never the launch profile's inherited grant."""
    from hermes_constants import get_hermes_home

    home = Path(env.get("HERMES_HOME") or get_hermes_home()).resolve()
    session_id = env.get("HERMES_SESSION_ID", "")
    inherited = env.pop(ENV_NAME, None)
    selected = session_environment(home, session_id)
    if selected:
        env.update(selected)
    elif inherited:
        try:
            grant = json.loads(inherited)
        except (ValueError, TypeError):
            return
        if isinstance(grant, dict) and grant.get("home") == str(home) and grant.get("session_id") == session_id:
            env[ENV_NAME] = inherited


def adopt_session_authority() -> bool:
    """Redeem the current home's grant; keep it across trusted interpreter re-exec."""
    raw = os.environ.get(ENV_NAME)
    if not raw:
        return False
    from hermes_constants import get_hermes_home

    home = get_hermes_home().resolve()
    try:
        grant = json.loads(raw)
        if (not isinstance(grant, dict) or grant.get("home") != str(home)
                or not isinstance(grant.get("token"), str)
                or not isinstance(grant.get("session_id"), str)
                or type(grant.get("port")) is not int or not 1 <= grant["port"] <= 65535):
            raise ValueError("invalid grant")
        with socket.create_connection(("127.0.0.1", grant["port"]), timeout=5) as connection:
            connection.sendall(json.dumps({k: grant[k] for k in ("token", "home", "session_id")}).encode() + b"\n")
            with connection.makefile("rb") as reader:
                response = reader.readline(_LIMIT + 1)
        payload = json.loads(response)
        owner = _vault_home(home)
        if not isinstance(payload, dict) or owner is None or payload.get("home") != str(owner):
            raise ValueError("refused grant")
        key = base64.b64decode(payload["key"], validate=True)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SessionAuthorityError("Session unlock is unavailable or belongs to another profile; reopen the owning session.") from exc
    from hermes_security.vault import adopt_unlocked_key

    adopt_unlocked_key(owner, key)
    return True


def _close_sessions() -> None:
    with _lock:
        authorities = list(_sessions.values())
        for authority in authorities:
            authority.references = 1
    for authority in authorities:
        authority.close()


atexit.register(_close_sessions)
