"""Transport abstraction for the tui_runtime JSON-RPC server.

A :class:`Transport` forwards a JSON-serialisable dict over the terminal's private stdio channel.
The request's transport lives in a
``ContextVar`` so pool-dispatched handlers write to the right peer; with nothing bound
``server.write_json`` falls back to the module-level :class:`StdioTransport`, which resolves
``_real_stdout`` lazily so tests that monkey-patch it keep working.
"""

from __future__ import annotations

import contextlib
import contextvars
import errno
import json
import logging
import os
import threading
from typing import Any, Callable, Optional, Protocol, runtime_checkable

# Errno values that mean "the peer is gone" rather than "the host has a real I/O problem". Anything
# outside this set re-raises so it surfaces in the crash log instead of looking like a clean disconnect.
_PEER_GONE_ERRNOS = frozenset({
    errno.EPIPE, errno.ECONNRESET, errno.EBADF, errno.ESHUTDOWN,
    getattr(errno, "WSAECONNRESET", -1), getattr(errno, "WSAESHUTDOWN", -1),  # win32 (no-op on POSIX)
} - {-1})

logger = logging.getLogger(__name__)

# When true, StdioTransport skips ``stream.flush`` after writing: on a half-closed pipe (TUI Node parent quit
# while the gateway still emits) flush can block long enough to starve the worker pool. Python text stdout is
# fully buffered on a pipe, so this ONLY makes sense with ``-u``/``PYTHONUNBUFFERED=1``; otherwise the TUI hangs.
_DISABLE_FLUSH = (os.environ.get("HERMES_TUI_GATEWAY_NO_FLUSH", "") or "").strip().lower() in {"1", "true", "yes", "on"}

@runtime_checkable
class Transport(Protocol):
    """Minimal interface every transport implements."""

    def write(self, obj: dict) -> bool:
        """Emit one JSON frame. Return ``False`` when the peer is gone."""

    def close(self) -> None:
        """Release any resources owned by this transport."""


_current_transport: contextvars.ContextVar[Optional[Transport]] = contextvars.ContextVar(
    "hermes_gateway_transport", default=None
)


def current_transport() -> Optional[Transport]:
    return _current_transport.get()


def bind_transport(transport: Optional[Transport]):
    """Bind *transport* for the current context; returns a token for :func:`reset_transport`."""
    return _current_transport.set(transport)


def reset_transport(token) -> None:
    _current_transport.reset(token)


def _raise_unless_peer_gone(exc: Exception, what: str) -> None:
    """Return when *exc* from a stream write/flush means the peer is gone; re-raise anything else.
    ``False`` from :meth:`StdioTransport.write` is the dispatcher's "broken stdout pipe" signal (``entry.py``
    exits cleanly on it), so programming errors and real host I/O bugs (UnicodeEncodeError from a misconfigured
    locale, ENOSPC, EACCES, ...) MUST re-raise so the crash log records them instead of masquerading as a clean
    disconnect. Peer-gone: BrokenPipeError, ValueError("...closed file..."), OSError errno in _PEER_GONE_ERRNOS."""
    if isinstance(exc, BrokenPipeError):
        return
    if isinstance(exc, ValueError):
        if isinstance(exc, UnicodeEncodeError) or "closed file" not in str(exc):
            raise exc
        return
    if not isinstance(exc, OSError) or exc.errno not in _PEER_GONE_ERRNOS:
        raise exc
    logger.debug("StdioTransport %s peer gone: %s", what, exc)


def serialize_frame(obj: dict, peer: str, log: logging.Logger) -> str:
    """``json.dumps`` the frame; an unserializable payload becomes a JSON-RPC error frame carrying
    the original id. Shared by every transport: without it the TypeError escaped from a pool
    worker (the executor swallows it), so the client waited forever with no log line (#92506)."""
    try:
        return json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        rid = obj.get("id") if isinstance(obj, dict) else None
        log.error("frame serialization failed peer=%s id=%s error_type=%s error=%s",
                  peer, rid, type(exc).__name__, exc)
        fallback = {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32603, "message": f"response serialization error: {exc}"}}
        return json.dumps(fallback, ensure_ascii=False)


class StdioTransport:
    """Writes JSON frames to a stream (usually ``sys.stdout``) resolved via a callable, so runtime
    monkey-patches of the stream keep working."""

    __slots__ = ("_lock", "_stream_getter")

    def __init__(self, stream_getter: Callable[[], Any], lock: threading.Lock) -> None:
        self._stream_getter = stream_getter
        self._lock = lock

    def write(self, obj: dict) -> bool:
        """Return ``True`` on success, ``False`` ONLY when the peer is gone (see :func:`_raise_unless_peer_gone`)."""
        # Serialization is OUTSIDE the lock so a large payload can't block other threads' frames.
        line = serialize_frame(obj, "stdio", logger) + "\n"
        with self._lock:
            stream = self._stream_getter()
            try:
                stream.write(line)
            except Exception as e:
                _raise_unless_peer_gone(e, "write")
                return False
            # A flush that *raises* peer-gone means the dispatcher should exit cleanly; one that *hangs*
            # on a half-closed pipe holds the lock until it returns — ``_DISABLE_FLUSH`` skips it entirely.
            if not _DISABLE_FLUSH:
                try:
                    stream.flush()
                except Exception as e:
                    _raise_unless_peer_gone(e, "flush")
                    return False
        return True

    def close(self) -> None:
        return None
