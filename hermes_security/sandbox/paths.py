"""In-process path checks for model-driven reads that do not spawn a process.

Terminal and file-tool shell operations are confined by the OS. A few tools read a
model-supplied path inside the Hermes process itself (vision, transcription, image
sources, the read/search pre-checks); they consult :func:`read_block_reason` so the
session's grants mean the same thing there. ``/proc`` and ``/sys`` are excluded from
the in-process allow-set: read from the Hermes process they describe Hermes itself.
"""

from __future__ import annotations

import os
from typing import Optional

_IN_PROCESS_EXCLUDED = ("/proc", "/sys")


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _spec(session_key: Optional[str] = None) -> Optional[dict]:
    from hermes_security.sandbox.policy import is_enabled
    if not is_enabled():
        return None
    from agent.delegation_context import is_delegated_child_context
    from hermes_security.sandbox.session import effective_policy
    return effective_policy(session_key).launch_spec(subagent=is_delegated_child_context())


def read_block_reason(path: str, *, session_key: Optional[str] = None) -> Optional[str]:
    spec = _spec(session_key)
    if spec is None:
        return None
    real = os.path.realpath(os.path.expanduser(path))
    for root in (*spec["read"], *spec["write"]):
        if any(_under(root, x) for x in _IN_PROCESS_EXCLUDED):
            continue
        if _under(real, os.path.realpath(root)):
            return None
    return (f"Access denied: {path} is outside this session's sandbox grants. Ask the user to "
            f"grant it (/sandbox grant {path}  — add :rw for write access).")


def write_block_reason(path: str) -> Optional[str]:
    """Early, readable refusal for a model write outside the session's ``rw`` grants (the
    OS would refuse it anyway, with a less useful error)."""
    spec = _spec()
    if spec is None:
        return None
    real = os.path.realpath(os.path.expanduser(path))
    if any(_under(real, os.path.realpath(root)) for root in spec["write"]):
        return None
    return (f"Access denied: {path} is not writable in this sandboxed session. Ask the user to "
            f"grant it (/sandbox grant {path}:rw).")


def is_explicit_file_grant(path: str) -> bool:
    """True when the session grants exactly this file (not merely a parent directory):
    the user named it, so it may be read even if it is a ``.env`` file."""
    from hermes_security.sandbox.policy import is_enabled
    if not is_enabled():
        return False
    from hermes_security.sandbox.session import effective_policy
    real = os.path.realpath(os.path.expanduser(path))
    return any(os.path.realpath(g.path) == real and os.path.isfile(g.path)
               for g in effective_policy(with_tmp=False).expanded_grants())
