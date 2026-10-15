"""Gateway runtime record I/O, independent of process identity and ownership policy."""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any, Optional

from utils import atomic_json_write


def read_json_file(path: Path, *, bare_pid_ok: bool = False) -> Optional[dict[str, Any]]:
    """JSON object at ``path``, or None when absent/empty/unreadable/invalid. ``bare_pid_ok`` also
    accepts legacy bare-integer PID files as ``{"pid": N}``."""
    try:
        # Fork: envelope-aware read inside a vaulted home.
        _text = None
        _home_for = None
        _seal_read = None
        try:
            from hermes_security.io import _home_for, read_text as _seal_read
        except ImportError:
            pass
        if _seal_read is not None and _home_for is not None and _home_for(path) is not None:
            _text = _seal_read(path, purpose="runtime-status")
        if _text is None:
            _text = path.read_text(encoding="utf-8-sig").strip() if path.exists() else ""
        raw = _text.strip()
    except (OSError, UnicodeDecodeError):  # vanished, EACCES, non-UTF-8 garbage
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
        if bare_pid_ok:
            with contextlib.suppress(ValueError):
                payload = int(raw)
    if bare_pid_ok and isinstance(payload, int):
        return {"pid": payload}
    return payload if isinstance(payload, dict) else None


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    # Fork: envelope-aware write inside a vaulted home.
    _home_for = None
    _seal_write = None
    try:
        from hermes_security.io import _home_for, write_json as _seal_write
    except ImportError:
        pass
    if _seal_write is not None and _home_for is not None and _home_for(path) is not None:
        _seal_write(path, payload, purpose="runtime-status")
        return
    atomic_json_write(path, payload, indent=None, separators=(",", ":"))


def write_json_excl(path: Path, record: dict[str, Any]) -> None:
    """Create ``path`` with O_CREAT|O_EXCL and dump ``record``; unlinks on a failed write."""
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
    except Exception:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
        raise

