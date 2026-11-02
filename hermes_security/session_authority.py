"""An unlocked session lends its own vault authority to local Hermes commands.

The passphrase is never retained. Children redeem an ephemeral local capability
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
import stat
import tempfile
import threading

from hermes_security.errors import VaultError, VaultLockedError

ENV_NAME = "HERMES_SESSION_VAULT"
_LIMIT = 8192
_lock = threading.RLock()
_sessions: dict[tuple[str, str], SessionAuthority] = {}
_adopted: dict[str, str] = {}


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
        response = self.server.authority.respond(request)
        self.wfile.write(json.dumps(response).encode("utf-8") + b"\n")


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    block_on_close = False
    address_family = socket.AF_UNIX if os.name == "posix" else socket.AF_INET


class SessionAuthority:
    def __init__(self, home: Path, session_id: str, vault_home: Path, *, parent=None, upstream=None, pipe=False) -> None:
        self.home, self.session_id, self.vault_home = home, session_id, vault_home
        self.parent, self.upstream = parent, upstream
        self.token = secrets.token_urlsafe(32)
        from agent.redact import register_vault_redaction_value
        register_vault_redaction_value(self.token)
        self.references = 1
        self.directory = None
        if pipe:
            from hermes_security.pipe_authority import PipeServer
            self.server = PipeServer(self.respond)
        else:
            self._socket_server()
        self.server.authority = self
        from agent.memory_provider import spawn_context_thread
        self.thread = spawn_context_thread(self.server.serve_forever, kwargs={"poll_interval": 0.05},
                                           name="hermes-session-unlock", daemon=True)
        self.thread.start()

    def _socket_server(self):
        address = ("127.0.0.1", 0)
        if os.name == "posix":
            from hermes_constants import socket_safe_tmpdir
            self.directory = tempfile.TemporaryDirectory(prefix="hermes-unlock-", dir=socket_safe_tmpdir())
            address = str(Path(self.directory.name) / "unlock.sock")
        try:
            self.server = _Server(address, _Handler)
        except BaseException:
            if self.directory is not None:
                self.directory.cleanup()
            raise
        if self.directory is not None:
            os.chmod(address, 0o600)

    def respond(self, request) -> dict:
        with _lock:
            live = self.is_live()
        token = request.get("token") if isinstance(request, dict) else None
        session_id = request.get("session_id") if isinstance(request, dict) else None
        identity = (str(self.home), session_id) if isinstance(session_id, str) else None
        if (not live or not isinstance(token, str) or not secrets.compare_digest(token, self.token)
                or request.get("home") != str(self.home) or _sessions.get(identity) is not self):
            return {"error": "session unlock does not authorize this home"}
        try:
            key = self.key()
        except (SessionAuthorityError, VaultError):
            return {"error": "the owning session is locked"}
        return {"home": str(self.vault_home), "key": base64.b64encode(key).decode("ascii")}

    def is_live(self) -> bool:
        """A derived grant cannot outlive the exact authority that issued it."""
        return (_sessions.get((str(self.home), self.session_id)) is self
                and (self.parent is None or self.parent.is_live()))

    def key(self) -> bytes:
        from hermes_security.vault import export_unlocked_key, is_unlocked
        if not is_unlocked(self.vault_home):
            raise VaultLockedError("The owning session is locked.")
        if self.upstream is not None:
            return _redeem(self.home, self.upstream)[1]
        if self.parent is not None:
            return self.parent.key()
        return export_unlocked_key(self.vault_home)

    def environment(self, *, session_id: str | None = None) -> dict[str, str]:
        from hermes_security.pipe_authority import PipeServer
        if isinstance(self.server, PipeServer):
            endpoint = {"pipe": str(self.server.path)}
        else:
            endpoint = self._socket_endpoint()
        return {ENV_NAME: json.dumps({**endpoint, "token": self.token,
                                      "home": str(self.home), "session_id": session_id or self.session_id})}

    def _socket_endpoint(self) -> dict:
        endpoint = ({"socket": self.server.server_address} if self.directory is not None
                    else {"port": self.server.server_address[1]})
        return endpoint

    def close(self) -> None:
        with _lock:
            identity = (str(self.home), self.session_id)
            if _sessions.get(identity) is not self:
                return
            self.references -= 1
            if self.references:
                return
            for alias, authority in list(_sessions.items()):
                if authority is self:
                    del _sessions[alias]
        self._stop_server()

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        if self.directory is not None:
            self.directory.cleanup()


def start_session_authority(home: Path | str, session_id: str, *, parent_session_id: str | None = None,
                            pipe: bool = False) -> SessionAuthority | None:
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
        parent = _sessions.get((str(home), parent_session_id)) if parent_session_id else None
        if parent_session_id and (parent is None or not parent.is_live()):
            raise SessionAuthorityError("The owning session ended before its child could start.")
        authority = SessionAuthority(home, session_id, owner, parent=parent, upstream=_adopted.get(str(home)), pipe=pipe)
        _sessions[identity] = authority
        return authority


def bind_agent_authority(agent, parent_session_id: str | None) -> None:
    """Construction seam: only actual delegated children derive a parent's grant."""
    from agent.delegation_context import is_delegated_child_context
    from hermes_constants import get_hermes_home
    agent._vault_session_authority = start_session_authority(
        get_hermes_home(), agent.session_id,
        parent_session_id=parent_session_id if is_delegated_child_context() else None,
    )


def reanchor_agent_authority(agent, session_id: str) -> None:
    """Compaction changes conversation identity, not the owning unlock's lifetime.

    Retain old aliases for already-running children; revoke all aliases together
    when the last owner reference closes.
    """
    authority = getattr(agent, "_vault_session_authority", None)
    if isinstance(authority, SessionAuthority):
        with _lock:
            identity = (str(authority.home), session_id)
            if not authority.is_live() or _sessions.get(identity, authority) is not authority:
                raise SessionAuthorityError("The compression continuation does not own this unlock.")
            _sessions[identity] = authority
            authority.session_id = session_id
    agent.session_id = session_id


def forget_inherited_authority(home: Path | None = None) -> None:
    """A later human unlock must not keep an earlier borrowed grant's provenance."""
    with _lock:
        for selected in list(_adopted):
            if home is None or _vault_home(Path(selected)) == home:
                del _adopted[selected]


def revoke_home_authorities(home: Path) -> None:
    """An explicit lock permanently revokes every grant backed by that vault.

    Detach identities before stopping servers so a concurrent new unlock cannot
    revive an old token or have its freshly registered authority closed instead.
    """
    with _lock:
        authorities = {authority for authority in _sessions.values() if authority.vault_home == home}
        for identity, authority in list(_sessions.items()):
            if authority in authorities:
                del _sessions[identity]
    for authority in authorities:
        authority._stop_server()


def session_environment(home: Path | str, session_id: str) -> dict[str, str]:
    with _lock:
        authority = _sessions.get((str(Path(home).resolve()), session_id))
        return authority.environment(session_id=session_id) if authority else {}


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


def _redeem(home: Path, raw: str) -> tuple[Path, bytes]:
    """Validate the owning process on every new redemption, including forwarded grants."""
    try:
        grant = json.loads(raw)
        if (not isinstance(grant, dict) or grant.get("home") != str(home)
                or not isinstance(grant.get("token"), str)
                or not isinstance(grant.get("session_id"), str)):
            raise ValueError("invalid grant")
        payload = _request_key(grant)
        owner = _vault_home(home)
        if not isinstance(payload, dict) or owner is None or payload.get("home") != str(owner):
            raise ValueError("refused grant")
        key = base64.b64decode(payload["key"], validate=True)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SessionAuthorityError("Session unlock is unavailable or belongs to another profile; reopen the owning session.") from exc
    return owner, key


def _request_key(grant: dict) -> dict:
    if "pipe" in grant:
        from hermes_security.pipe_authority import redeem
        return redeem(grant)
    with _connection(grant) as connection:
        connection.sendall(json.dumps({k: grant[k] for k in ("token", "home", "session_id")}).encode() + b"\n")
        with connection.makefile("rb") as reader:
            return json.loads(reader.readline(_LIMIT + 1))


def _connection(grant: dict) -> socket.socket:
    """New POSIX grants use owner-only filesystem sockets, including without IP networking.

    Read old loopback grants so a running pre-update session can finish its local update.
    """
    if "socket" not in grant:
        port = grant.get("port")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("invalid grant endpoint")
        return socket.create_connection(("127.0.0.1", port), timeout=5)
    path = grant["socket"]
    if not isinstance(path, str) or not Path(path).is_absolute() or os.name != "posix":
        raise ValueError("invalid grant endpoint")
    info, parent = Path(path).lstat(), Path(path).parent.lstat()
    if (not stat.S_ISSOCK(info.st_mode) or not stat.S_ISDIR(parent.st_mode)
            or info.st_uid != os.getuid() or parent.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or stat.S_IMODE(parent.st_mode) != 0o700):
        raise ValueError("unprotected grant endpoint")
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(5)
        connection.connect(path)
    except BaseException:
        connection.close()
        raise
    return connection


def adopt_session_authority() -> bool:
    """Redeem the current home's grant without turning borrowed unlock into a new root."""
    raw = os.environ.get(ENV_NAME)
    if not raw:
        return False
    from hermes_constants import get_hermes_home
    home = get_hermes_home().resolve()
    owner, key = _redeem(home, raw)
    from hermes_security.vault import adopt_unlocked_key
    adopt_unlocked_key(owner, key)
    with _lock:
        _adopted[str(home)] = raw
    return True


def _close_sessions() -> None:
    with _lock:
        authorities = list(_sessions.values())
        for authority in authorities:
            authority.references = 1
    for authority in authorities:
        authority.close()


atexit.register(_close_sessions)
