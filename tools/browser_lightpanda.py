"""Lightpanda local engine for Browser Use mode.

With ``browser.engine: lightpanda``, Browser Use mode spawns one ``lightpanda serve`` per
browser session and points ``browser_exec`` at its CDP endpoint (``BU_CDP_URL``); the
built-in ``browser_*`` tools keep going through ``agent-browser --engine lightpanda``.
``tools.browser_tool`` owns the session cache, inactivity reaper and atexit sweep.
"""

import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LIGHTPANDA_INSTALL_URL = "https://lightpanda.io/docs/run-locally/installation/one-liner"
LIGHTPANDA_INSTALL_HINT = f"Install Lightpanda from {LIGHTPANDA_INSTALL_URL} and make sure " "`lightpanda` is on PATH"

_READY_TIMEOUT_S = 10.0
_POLL_INTERVAL_S = 0.1
_STDERR_TAIL_LIMIT = 2000

_servers: "dict[tuple[Path, str], LightpandaServer]" = {}
_servers_lock = threading.Lock()


@dataclass
class LightpandaServer:
    """proc/start_time name the supervisor; lease.record names the actual native vendor."""
    session_name: str
    port: int
    proc: subprocess.Popen
    log_path: str
    start_time: Optional[int] = None
    lease: object = None
    users: dict = field(default_factory=dict)

    @property
    def cdp_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"  # HTTP discovery URL; the harness resolves /json/version itself

    def is_alive(self) -> bool:
        if self.lease is not None:
            self.lease.resource.custody.check()
        return (self.proc.poll() is None and self.lease is not None and self.lease.record is not None
                and _record_matches(self.lease.record))

    def admit(self):
        from agent.session_context import get_session_env
        from hermes_security.session_custody import SessionCustody

        source = get_session_env('HERMES_SESSION_ID', '')
        if source:
            self.users[source] = SessionCustody.capture(self.lease.home, resource=self.lease.resource.custody)

    def shared_with_other(self, closing_source):
        from hermes_security.errors import VaultLockedError

        self.lease.resource.custody.check()
        for source, operation in self.users.items():
            if source == closing_source:
                continue
            try:
                operation.check()
            except VaultLockedError:
                continue
            return True
        return False


def _home_candidates() -> list:
    home = Path.home()
    candidates = [home / ".lightpanda" / "lightpanda", home / ".local" / "bin" / "lightpanda"]
    try:
        from hermes_constants import get_hermes_home
        candidates.append(Path(get_hermes_home()) / "bin" / "lightpanda")
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("hermes home unavailable for lightpanda lookup: %s", e)
    return candidates


def find_lightpanda_binary() -> Optional[str]:
    """Return the lightpanda executable, or None. Order: PATH (with agent-browser's Homebrew/managed-node
    fallbacks), then installer/agent-browser locations, then ``$HERMES_HOME/bin``. No Windows build."""
    if os.name == "nt":
        logger.debug("Lightpanda has no Windows build")
        return None
    path_env = os.environ.get("PATH", "")
    try:
        from tools.browser_tool_install import _merge_browser_path
        path_env = _merge_browser_path(path_env)
    except Exception as e:
        logger.debug("browser PATH merge unavailable: %s", e)
    return shutil.which("lightpanda", path=path_env) or next(
        (str(c) for c in _home_candidates() if c.is_file() and os.access(c, os.X_OK)), None)


def _pick_free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


_CAPABILITIES = {}


_HTTP_CACHE_FLAG = '--http-cache-dir'


def _binary_supports_http_cache(binary: str, env: dict) -> bool:
    """Probe public executable capabilities with the exact scrubbed private vendor environment."""
    from tools.browser_lightpanda_state import vendor_environment

    env = vendor_environment(env)
    info = Path(binary).stat()
    identity = str(Path(binary).resolve()), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
    if identity not in _CAPABILITIES:
        _CAPABILITIES.clear()  # Public executable capability cache remains bounded to one selection.
        try:
            proc = subprocess.run([binary, 'help'], env=env, capture_output=True, text=True,
                                  encoding='utf-8', errors='replace', timeout=3.0,
                                  stdin=subprocess.DEVNULL, check=False)
            _CAPABILITIES[identity] = _HTTP_CACHE_FLAG in ((proc.stdout or '')+(proc.stderr or ''))
        except (OSError, subprocess.SubprocessError) as error:
            logger.debug('Lightpanda capability probe failed: %s', error)
            _CAPABILITIES[identity] = False
    return _CAPABILITIES[identity]


def _browser_env() -> dict:
    from tools.browser_tool import _build_browser_env

    return _build_browser_env()  # Refused original scope never falls back to ambient authority.


def _cdp_ready(url: str, timeout: float = 0.2) -> bool:
    try:
        from hermes_cli.browser_connect import is_browser_debug_ready
        return is_browser_debug_ready(url, timeout=timeout)
    except Exception as e:
        logger.debug("CDP readiness probe failed for %s: %s", url, e)
        return False


def _read_log_tail(path: str) -> str:
    """Last non-empty line of the child's stderr log ('' when unreadable)."""
    try:
        text = Path(path).read_bytes()[-_STDERR_TAIL_LIMIT:].decode("utf-8", errors="replace")
    except OSError:
        return ""
    return next((ln for ln in reversed(text.strip().splitlines()) if ln.strip()), "")


def _terminate(proc: subprocess.Popen, what: str = "lightpanda") -> None:
    """poll -> terminate -> wait(5) -> kill; shared with the real-profile Chrome cleanup."""
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
    except Exception as e:
        logger.debug("%s terminate failed: %s", what, e)


def _process_record(child, binary, argv, env):
    from hermes_platform.lightpanda_process import owned_child_record

    return owned_child_record(child, executable=binary, argv=argv, home=env['HOME'])


def _record_matches(record):
    from hermes_platform.lightpanda_process import matches

    return matches({key:value for key,value in record.items() if key != 'kind'})


def _key(session_name, home=None):
    from hermes_constants import get_hermes_home

    return Path(home or get_hermes_home()).resolve(), session_name


def check_resource_epoch(resource):
    with _servers_lock:
        residents = [server for server in _servers.values() if server.lease.home == resource.home]
    for server in residents:
        original = server.lease.resource.custody
        if original.vault is not resource.custody.vault:
            raise RuntimeError('An earlier Lightpanda unlock still has retained resident state; close its original owner first')
        original.check()
        if original.authority is not None and not original.authority.is_current():
            raise RuntimeError('An earlier Lightpanda native lineage still has retained resident state; close its original owner first')


def _register(server):
    with _servers_lock:
        key = server.lease.home, server.session_name
        if key in _servers:
            raise RuntimeError('The original Lightpanda session is still tracked; state retained')
        _servers[key] = server
        server.users[server.lease.source] = server.lease.source_operation


def _ready(server, argv, env, call):
    deadline = time.monotonic()+_READY_TIMEOUT_S
    while True:
        server.lease.check(call)
        rc = server.proc.poll()
        if rc is not None:
            raise RuntimeError(f'Lightpanda exited with code {rc} before CDP readiness: {_read_log_tail(server.log_path)}')
        record = _process_record(server.proc, argv[0], argv, env)
        if (record is not None and _cdp_ready(server.cdp_url)
                and _process_record(server.proc, argv[0], argv, env) == record):
            server.lease.identify(server.proc, record, call)
            server.start_time = server.lease.supervisor_record['start_time']
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f'Lightpanda did not establish its exact process and CDP endpoint within {int(_READY_TIMEOUT_S)}s')
        time.sleep(_POLL_INTERVAL_S)


def _launch(lease, binary, *, block_private_networks):
    from hermes_platform.managed_supervision import SupervisedChild

    with lease.access() as call:
        lease.prepare(call)
        env = lease.environment(_browser_env())
        port = _pick_free_loopback_port()
        argv = [binary, 'serve', '--host', '127.0.0.1', '--port', str(port)]
        if _binary_supports_http_cache(binary, env):
            argv.extend([_HTTP_CACHE_FLAG, str(lease.cache)])
        if block_private_networks:
            argv.append('--block-private-networks')
        lease.check(call)
        lease.starting(binary, call)
        descriptor = os.open(lease.log, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC, 0o600)
        with os.fdopen(descriptor, 'wb') as log:
            child = SupervisedChild(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=log, start_new_session=True)
        lease.child = child
        server = LightpandaServer(lease.session, port, child, str(lease.log), lease=lease)
        _register(server)
        _ready(server, argv, env, call)
    logger.info('Lightpanda private cache %s; encrypted checkpoint %s', lease.cache, lease.home/lease.resource.relative)
    return server


def launch_lightpanda(session_name: str, *, block_private_networks: bool = False):
    """Launch only inside original profile authority and proven RAM, retaining every failed copy."""
    from tools.browser_terminal import browser_in_terminal
    from hermes_constants import get_hermes_home
    from hermes_security.errors import VaultError
    from tools.browser_lightpanda_state import LightpandaLease

    if browser_in_terminal():
        return None, 'Native Lightpanda cannot run outside the selected terminal sandbox; use an explicit outgoing CDP endpoint.'
    binary = find_lightpanda_binary()
    if not binary:
        return None, f"browser.engine is 'lightpanda' but no executable is selected. {LIGHTPANDA_INSTALL_HINT}"
    lease = None
    try:
        lease = LightpandaLease(get_hermes_home(), session_name)
        return _launch(lease, binary, block_private_networks=block_private_networks), None
    except (OSError, RuntimeError, ValueError, VaultError) as error:
        if lease is not None and lease.child is not None:
            # This is cleanup of our exact supervisor, never a signal based on old PID metadata.
            status = lease.child.cancel()
            logger.warning('Lightpanda launch refused; owned cleanup status %s, private RAM retained at %s', status, lease.resource.path)
        location = f'; private RAM retained at {lease.resource.path}' if lease is not None else ''
        return None, f'Lightpanda launch refused: {error}{location}'


def _lookup(session_name, home=None):
    with _servers_lock:
        return _servers.get(_key(session_name, home))


def get_server(session_name: str, *, home=None):
    server = _lookup(session_name, home)
    if server is not None:
        server.admit()
    return server


def stop_lightpanda(session_name: str, *, home=None, expected=None, operation=None):
    """Stop only the exact original named server; a failed close/save retains all tracking."""
    from hermes_security.session_custody import SessionCustody

    key = _key(session_name, home)
    with _servers_lock:
        server = _servers.get(key)
    if server is None:
        return
    lease = server.lease
    operation = _cleanup_operation(server, operation)
    if expected is not None and lease.token != expected:
        raise RuntimeError('Original Lightpanda session was replaced; no process was stopped')
    if operation is None:
        operation = SessionCustody.capture(lease.home, resource=lease.resource.custody)
    operation.check()
    source = lease.source if operation in (lease.operation, lease.source_operation) else _current_source()
    if server.shared_with_other(source):
        server.users.pop(source, None)
        logger.info('Lightpanda session remains shared by another live source; cache checkpoint deferred')
        return
    with lease.access(operation) as call:
        if server.proc.poll() is None:
            if lease.record is None or not _record_matches(lease.record):
                raise RuntimeError('Original Lightpanda process identity is unknown; RAM and archive retained')
            call.check()
            if server.proc.cancel() is None:
                raise RuntimeError('Lightpanda owned tree cleanup is unconfirmed; all state retained')
        server.proc.wait(timeout=5)
    lease.finish(operation)
    with _servers_lock:
        if _servers.get(key) is not server:
            raise RuntimeError('Lightpanda tracking changed during cleanup; all state retained')
        del _servers[key]
        remaining = any(other.lease.resource is lease.resource for other in _servers.values())
    if not remaining:
        lease.resource.release(operation=operation)


def stop_all_lightpanda():
    with _servers_lock:
        servers = list(_servers.values())
    for server in servers:
        try:
            stop_lightpanda(server.session_name, home=server.lease.home,
                            expected=server.lease.token, operation=server.lease.source_operation)
        except (OSError, RuntimeError) as error:
            logger.warning('Lightpanda cache remains pending at %s: %s', server.lease.resource.path, error)


def reap_orphaned_lightpanda():
    """Owned supervisors handle parent death. Old raw records never authorize signals or deletion."""
    from hermes_constants import get_hermes_home
    from tools.browser_lightpanda_state import legacy_path

    if os.path.lexists(legacy_path(get_hermes_home())):
        logger.warning('Legacy Lightpanda data retained; use explicit browser.worker.repair')
    with _servers_lock:
        dead = [server for server in _servers.values() if not server.is_alive()]
    reaped = 0
    for server in dead:
        try:
            stop_lightpanda(server.session_name, home=server.lease.home,
                            expected=server.lease.token, operation=server.lease.source_operation)
            reaped += 1
        except (OSError, RuntimeError) as error:
            logger.warning('Lightpanda pending recovery retained at %s: %s', server.lease.resource.path, error)
    return reaped


from contextlib import contextmanager
from contextvars import ContextVar

_CLEANUP = ContextVar('lightpanda_cleanup', default=None)


def _current_source():
    from agent.session_context import get_session_env

    return get_session_env('HERMES_SESSION_ID', '')


def _cleanup_operation(server, provided):
    selected = _CLEANUP.get()
    if selected is not None:
        original, operation = selected
        if original is not server:
            raise RuntimeError('The queued Lightpanda cleanup belongs to another server generation')
        return operation
    return provided


@contextmanager
def cleanup_scope(info, *, home=None, original=False):
    """Queued cleanup retains original Source; a fresh explicit request captures its own operation."""
    from hermes_security.session_custody import SessionCustody

    if not (info.get('features') or {}).get('lightpanda'):
        yield
        return
    server = _lookup(info.get('session_name', ''), home)
    if server is None:
        yield
        return
    if info.get('lightpanda_epoch') != server.lease.token:
        raise RuntimeError('Original Lightpanda session generation changed; state retained')
    existing = _CLEANUP.get()
    if existing is not None:
        if existing[0] is not server:
            raise RuntimeError('A different Lightpanda cleanup operation is already selected')
        operation = existing[1]
    else:
        operation = (server.lease.source_operation if original else
                     SessionCustody.capture(server.lease.home, resource=server.lease.resource.custody))
    operation.check()
    token = _CLEANUP.set((server, operation))
    try:
        yield
    finally:
        _CLEANUP.reset(token)
