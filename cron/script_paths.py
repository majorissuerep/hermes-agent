"""Cron script-path resolution and interpreter recognition."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

_INTERPRETER_IMAGE_RE = re.compile(r"^pythonw?(?:\d+(?:\.\d+)*)?(?:\.exe)?$")

def _expand_candidate_path(candidate: str) -> Optional[Path]:
    """Sanitize a tokenized path candidate at the ingestion boundary. Tokens from shlex-splitting
    arbitrary (possibly binary-decoded) text can carry NUL or junk that each downstream ``Path`` op
    rejects differently (ValueError, RuntimeError when HOME is unset under launchd, OSError); reject
    once here. ``None`` = not a real path, nothing to scan.

    Every OS-facing ``Path`` operation downstream (``expanduser``, ``os.open``, ``resolve``) raises a
    *different* exception for the same junk (``ValueError: embedded null byte``, ``RuntimeError: Could not
    determine home directory`` when HOME is unset under launchd, OSError for over-long paths). Rejecting
    here — once, before any OS call — is the whole-class fix; catching per-syscall was the whack-a-mole that
    produced #76762, #77703, #77780, and #78256.
    """
    if not candidate or "\x00" in candidate:
        return None
    try:
        return Path(candidate).expanduser()
    except (ValueError, RuntimeError, OSError):
        return None


def _resolve_script_path(script_path: str) -> Optional[Path]:
    """Resolve a cron ``script`` value the way ``cron.scheduler`` does (relative paths live under
    ``<HERMES_HOME>/scripts/``) so the guard scans the file that will actually run."""
    from hermes_constants import get_hermes_home

    raw = _expand_candidate_path(script_path)
    if raw is None:
        return None
    if raw.is_absolute():
        return raw
    try:
        return get_hermes_home() / "scripts" / raw
    except (RuntimeError, OSError):
        # get_hermes_home() falls back to Path.home(), which raises when neither HERMES_HOME nor
        # HOME is resolvable (launchd/systemd) — same ingestion contract: nothing to scan.
        return None
