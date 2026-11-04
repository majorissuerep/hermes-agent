"""Opt-in ``browser.record_sessions`` video: RAM-only while recording, encrypted when retained.

A recording binds, at start, the exact owning home, that home's original vault custody
(``hermes_security.artifacts._Custody``), the browser session key and the execution target.
The native browser writes into an owned 0700 proven-RAM workspace held for the recording's
whole lifetime. A browser in the explicitly selected outgoing terminal writes into that
backend's proven-RAM capture directory; only the backend path is passed to it, and a backend
that cannot prove one refuses recording rather than falling back to the host.

On a valid completed ``record stop`` the allocated capture (never a daemon-reported path) is
read once with a hard byte bound and sealed under ``artifacts/media/videos``. Only after the
encrypted copy is published is the RAM capture retired. A sealing failure keeps the RAM bytes
and the pending entry; the next cleanup of that session (or of all sessions) retries with the
ORIGINAL custody, so a lock followed by a fresh unlock never publishes a stale recording.

Legacy plaintext ``browser_recordings/session_*.webm`` files from older builds are left
untouched for the owner's explicit offline repair; retention prunes only current encrypted
recordings of this profile.
"""
from __future__ import annotations

from contextvars import Context, copy_context
import logging
import os
import posixpath
import re
import stat
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

VIDEO_SUBDIR = ("artifacts", "media", "videos")
RETENTION_HOURS = 72
_NAME = re.compile(r"browser_recording_\d{8}T\d{6}Z_[0-9a-f]{32}\.webm")
_CLIENT_ENV = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SSH_AUTH_SOCK",
               "SSH_AGENT_PID", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH",
               "DOCKER_TLS_VERIFY", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY",
               "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy")


class RecordingError(RuntimeError):
    """A recording could not be completed or sealed; its original capture is retained."""


@dataclass
class _Recording:
    session_key: str
    home: Path
    custody: Any
    name: str
    lease: Any
    context: Context
    session_info: dict
    browser_cmd: str
    engine: str
    backend: str
    client_env: dict
    env: Any = None  # outgoing terminal environment captured at start (None = native)
    runtime: Any = None
    capture: str = ""  # path the browser was told to write (backend path for outgoing)
    state: str = "recording"  # recording | stopping | sealing | unstopped
    error: str = field(default="")
    complete: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)  # per-generation seal lock

    @property
    def outgoing(self) -> bool:
        return self.env is not None

    @property
    def stage(self) -> Path:
        return self.lease.stage


def _log(rec: _Recording, level: int, message: str, *args) -> None:
    rec.context.copy().run(logger.log, level, message, *args)


_LOCK = threading.Lock()
_RECORDINGS: dict[str, _Recording] = {}
_STARTING: set[str] = set()


def _max_video_bytes() -> int:
    from hermes_security.artifacts import _MAX_VIDEO_BYTES
    return _MAX_VIDEO_BYTES


def _enabled() -> bool:
    from hermes_cli.config import cfg_get, read_raw_config
    return bool(cfg_get(read_raw_config(), "browser", "record_sessions", default=False))


def _new_name() -> str:
    return f"browser_recording_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{uuid.uuid4().hex}.webm"


def _capture_context(home: Path, custody: Any) -> Context:
    from agent.secret_scope import (build_profile_secret_scope, current_secret_scope,
                                    current_secret_scope_home, set_multiplex_context, set_secret_scope)
    from hermes_constants import set_hermes_home_override

    custody.check()
    scope, owner = current_secret_scope(), current_secret_scope_home()
    if owner is not None and Path(owner).resolve() != home:
        raise RecordingError("The browser recording secret scope belongs to another profile")
    secrets = dict(scope) if scope is not None and owner is not None else build_profile_secret_scope(home)
    context = copy_context()

    def bind():
        set_hermes_home_override(home)
        set_secret_scope(secrets, profile_home=str(home))
        set_multiplex_context(True)  # a missed secret must never borrow a later ambient profile

    context.run(bind)
    return context


def _capture_driver(session_key: str, home: Path):
    from tools import browser_tool_session as session
    from tools.browser_tool_origin import origin_module
    from tools import browser_tool_cloud as cloud

    browser = origin_module()
    with browser._cleanup_lock:
        info = browser._active_sessions.get(session_key)
        owner = browser._session_owner_homes.get(session_key)
    if info is None or (owner is not None and Path(owner).resolve() != home):
        raise RecordingError("The browser recording has no active session owned by this profile")
    prepared = session._browser_command_preflight()
    if "browser_cmd" not in prepared:
        raise RecordingError(str(prepared.get("error") or "The browser recorder is unavailable"))
    return info, prepared["browser_cmd"], cloud._get_browser_engine()


def _outgoing_target(name: str):
    """Capture the selected existing backend and its proven RAM runtime once."""
    from tools import browser_terminal as terminal

    env = terminal.terminal_environment(create=True)
    runtime = terminal.browser_runtime(env)
    return env, runtime, posixpath.join(runtime.captures, name)


def _owned_call(rec: _Recording, action, *args):
    from tools import browser_terminal as terminal

    def call():
        rec.custody.check()
        with terminal.captured_execution(rec.home, rec.backend, rec.env, rec.runtime):
            return action(*args)

    return rec.context.copy().run(call)


def _command(rec: _Recording, args: list[str]):
    from tools import browser_tool_session as session

    return session._run_recording_command(rec.session_key, rec.session_info, rec.browser_cmd, rec.engine, args)


def _remove_backend_capture(rec: _Recording) -> None:
    from tools.environments import streams

    result = streams.run_in(rec.env, ["rm", "-f", "--", rec.capture], timeout=15)
    if result.returncode:
        raise RecordingError("The outgoing RAM recording could not be removed after encrypted publication")


def _allocate(session_key: str) -> Optional[_Recording]:
    """Bind home/custody/target and allocate the RAM capture; None when recording must not start."""
    from hermes_constants import get_hermes_home
    from hermes_security import artifacts
    from hermes_security.errors import VaultError

    home = Path(get_hermes_home()).resolve()
    try:
        custody = artifacts._bound_custody(home)
        context = _capture_context(home, custody)
        return context.run(_allocate_owned, session_key, home, custody, context)
    except (VaultError, OSError, ValueError, RuntimeError) as exc:
        logger.warning("browser.record_sessions: not recording %s; its original profile or target is unavailable (%s)",
                       session_key, exc)
        return None


def _allocate_owned(session_key: str, home: Path, custody: Any, context: Context) -> _Recording:
    from tools import browser_terminal as terminal
    from tools import browser_recording_pending as pending

    info, browser_cmd, engine = _capture_driver(session_key, home)
    name = _new_name()
    backend = terminal.terminal_backend()
    target = _outgoing_target(name) if backend != "local" else (None, None, "")
    lease = pending.allocate(home, custody, name)
    client_env = {name: os.environ[name] for name in _CLIENT_ENV if name in os.environ}
    rec = _Recording(session_key, home, custody, name, lease, context, info, browser_cmd, engine, backend, client_env,
                     capture=str(lease.stage))
    if backend != "local":
        rec.env, rec.runtime, rec.capture = target
    return rec


def _discard(rec: _Recording) -> None:
    """Remove an unpublished capture that is not anyone's only retained copy."""
    from tools import browser_recording_pending as pending

    pending.discard_empty(rec.lease, rec.custody)


def start(session_key: str) -> str | None:
    """Start recording ``session_key`` when ``browser.record_sessions`` is enabled."""
    with _LOCK:
        if session_key in _RECORDINGS or session_key in _STARTING:
            return
        _STARTING.add(session_key)
    try:
        if not _enabled():
            return
        rec = _allocate(session_key)
        if rec is None:
            return "Browser recording did not start; check this profile's vault and protected workspace settings."
        prune_expired(rec.home, custody=rec.custody)
        return _begin(rec)
    finally:
        with _LOCK:
            _STARTING.discard(session_key)


def _begin(rec: _Recording) -> str | None:
    started = False
    try:
        try:
            result = _owned_call(rec, _command, rec, ["start", rec.capture])
        except (OSError, ValueError, RuntimeError) as exc:
            _log(rec, logging.ERROR, "Browser recording start failed for %s: %s", rec.session_key, exc)
            result = {"success": False, "error": str(exc)}
        started = bool(result.get("success"))
        if not started:
            _log(rec, logging.WARNING, "browser.record_sessions: could not start recording %s: %s",
                           rec.session_key, result.get("error"))
            return "Browser recording did not start: " + str(result.get("error") or "the recorder refused")
        with _LOCK:
            _RECORDINGS[rec.session_key] = rec
        _log(rec, logging.INFO, "Recording browser session %s into protected RAM", rec.session_key)
    finally:
        if not started:
            try:
                _owned_call(rec, _discard, rec)
            except (OSError, ValueError, RuntimeError) as exc:
                with _LOCK:
                    _RECORDINGS[rec.session_key] = rec
                _retain_unstopped(rec, f"record start was not acknowledged; allocation retained: {exc}")


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _fetch_capture_bytes(rec: _Recording) -> bytes:
    """A bounded anonymous-pipe transfer from the original backend; no execute/log spool."""
    from hermes_platform.client_capture import run_client
    from hermes_platform.remote_workspace import regular_file_read_argv
    from tools.environments import streams

    limit = _max_video_bytes()
    argv = rec.runtime.guarded(regular_file_read_argv(rec.capture, max_bytes=limit))
    command = streams.remote_command(rec.env, argv, interactive=False)
    if command is None:
        raise RecordingError("The original backend cannot stream its recording")
    code, data, _stderr = run_client(command, env=rec.client_env, timeout=120, max_output=limit + 65536)
    if code != 0 or len(data) > limit:
        raise RecordingError("The original recording transfer failed or exceeded its bound; it was retained")
    return data


def _seal(rec: _Recording):
    from tools import browser_recording_pending as pending

    rec.custody.check()  # original custody before any backend effect, read or publish
    if not rec.complete:
        if rec.outgoing:
            pending.store_capture(rec.lease, rec.custody, _fetch_capture_bytes(rec))
        pending.completed(rec.lease, rec.custody)
        rec.complete = True
    return pending.publish_and_retire(rec.lease, rec.custody)


def _publish(rec: _Recording) -> Optional[Path]:
    """Seal ``rec``; retire the RAM capture only after publication, else keep it pending."""
    from hermes_security.errors import VaultError

    if not rec.lock.acquire(blocking=False):
        return None  # a concurrent cleanup/retry already owns this exact generation
    try:
        sealed, cleanup_problem = _owned_call(rec, _seal, rec)
    except (VaultError, OSError, ValueError, RuntimeError) as exc:
        rec.state, rec.error = "sealing", str(exc)
        location = ("on the original selected outgoing backend; no complete local copy was confirmed"
                    if rec.outgoing and not rec.complete else f"in protected RAM at {rec.stage}")
        _log(rec, logging.ERROR, "Browser recording for %s was NOT saved (%s). Its capture remains %s; "
                     "closing this browser session again retries with the original vault unlock.",
                     rec.session_key, exc, location)
        return None
    finally:
        rec.lock.release()
    with _LOCK:
        if _RECORDINGS.get(rec.session_key) is rec:  # never a newer generation under the same key
            del _RECORDINGS[rec.session_key]
    if cleanup_problem:
        _log(rec, logging.WARNING, "Recording saved encrypted at %s; RAM/locator cleanup is incomplete: %s",
                       sealed, cleanup_problem)
    if rec.outgoing:
        try:
            _owned_call(rec, _remove_backend_capture, rec)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            _log(rec, logging.WARNING, "Recording saved encrypted at %s; its original backend RAM capture remains: %s", sealed, exc)
    _log(rec, logging.INFO, "Saved encrypted browser recording for %s: %s", rec.session_key, sealed)
    return sealed


def stop(session_key: str) -> Optional[Path]:
    """Stop an active recording (before the session closes) or retry a pending seal.

    Returns the encrypted artifact path when one was published."""
    with _LOCK:
        rec = _RECORDINGS.get(session_key)
        state = rec.state if rec is not None else ""
        if state == "recording":
            rec.state = "stopping"  # once-only: claimed before any I/O; never replayed
    if state == "sealing":
        return _publish(rec)
    if state != "recording":
        return None
    try:
        result = _owned_call(rec, _command, rec, ["stop"])
    except (OSError, ValueError, RuntimeError) as exc:
        _log(rec, logging.ERROR, "Browser recording stop for %s failed: %s", session_key, exc)
        result = {"success": False, "error": str(exc)}
    if not result.get("success"):
        _retain_unstopped(rec, str(result.get("error") or "record stop failed"))
        return None
    return _publish(rec)


def _retain_unstopped(rec: _Recording, reason: str) -> None:
    rec.state, rec.error = "unstopped", reason
    where = f"{rec.capture} in the selected backend" if rec.outgoing else str(rec.stage)
    _log(rec, logging.ERROR, "Browser recording for %s did not stop cleanly (%s); the incomplete capture is "
                 "retained in protected RAM at %s and was not saved.", rec.session_key, reason, where)


def abandon(session_key: str) -> None:
    """Force-reap path: no ``record stop`` is possible; keep the incomplete capture in RAM."""
    with _LOCK:
        rec = _RECORDINGS.get(session_key)
    if rec is not None and rec.state == "recording":
        _retain_unstopped(rec, "the browser session was force-reaped")


def retry_seal(session_key: str) -> None:
    """Retry a pending seal for a session whose browser is already gone (no browser command)."""
    with _LOCK:
        rec = _RECORDINGS.get(session_key)
    if rec is not None and rec.state == "sealing":
        _publish(rec)


def retry_pending() -> None:
    """Retry every pending seal with its original custody (existing cleanup-all path)."""
    with _LOCK:
        keys = [key for key, rec in _RECORDINGS.items() if rec.state == "sealing"]
    for key in keys:
        retry_seal(key)


def report_unsaved() -> None:
    """At exit: name every recording that was not saved; its RAM copy is left in place."""
    with _LOCK:
        unsaved = [rec for rec in _RECORDINGS.values() if rec.state not in ("recording", "stopping")]
    for rec in unsaved:
        where = f"{rec.capture} in the selected backend" if rec.outgoing else str(rec.stage)
        _log(rec, logging.ERROR, "Unsaved browser recording for %s (%s: %s) remains only in protected RAM at %s; "
                     "it is lost when that RAM is cleared.", rec.session_key, rec.state, rec.error, where)


def prune_expired(home: Path, *, custody: Any, max_age_hours: float = RETENTION_HOURS) -> None:
    """Delete this profile's current encrypted recordings older than ``max_age_hours``.

    Only exact current names in the owned 0700 video directory that AUTHENTICATE under the
    original custody are deleted (at most ``_PRUNE_PER_RUN`` per call); legacy
    ``browser_recordings`` and other video producers are untouched."""
    from hermes_security import artifacts
    from hermes_security.errors import VaultError

    relative = Path(*VIDEO_SUBDIR, "browser_recording_probe.webm")
    cutoff = time.time() - max_age_hours * 3600
    try:
        custody.check()
        with artifacts._parent(home, relative) as parent:
            expired = [entry.name for entry in os.scandir(parent) if _expired(entry, cutoff)]
            for name in expired[:_PRUNE_PER_RUN]:
                _prune_authenticated(home, parent, name, custody)
    except FileNotFoundError:
        return
    except (VaultError, PermissionError, OSError) as exc:
        logger.warning("Browser recording retention skipped: %s", exc)


_PRUNE_PER_RUN = 4  # bounded: each candidate is fully authenticated (up to 200 MiB) first


def _expired(entry: os.DirEntry, cutoff: float) -> bool:
    if not _NAME.fullmatch(entry.name):
        return False
    info = entry.stat(follow_symlinks=False)
    return stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_mtime < cutoff


def _prune_authenticated(home: Path, parent: int, name: str, custody: Any) -> None:
    """Delete one expired current recording only after it authenticates under ORIGINAL custody.

    A damaged/unsealed current-name file is retained and reported; the inode is re-proven
    immediately before unlink so a replacement is never removed."""
    from hermes_security import artifacts
    from hermes_security.errors import VaultError

    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    try:
        artifacts.read_bytes(home.joinpath(*VIDEO_SUBDIR) / name, home=home, custody=custody)
    except (VaultError, ValueError) as exc:
        logger.warning("Retaining expired browser recording %s: it did not authenticate (%s)", name, exc)
        return
    custody.check()
    after = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if _identity(after) != _identity(before) or after.st_mtime_ns != before.st_mtime_ns:
        logger.warning("Retaining browser recording %s: it changed during retention", name)
        return
    os.unlink(name, dir_fd=parent)
