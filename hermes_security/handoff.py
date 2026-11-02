"""Hand an unlocked vault to a spawned child process — no password in env, argv or on disk.

The parent writes the master key into a pipe and passes only the READ END as an inherited fd; the
child reads it once at its vault gate, closes it, and proves the key against the vault verifier
before caching it (``vault.adopt_unlocked_key``). The key never exists anywhere but the two processes'
memory and a kernel pipe buffer that is drained on first read. Local TUI, dependency-interpreter
and encrypted-output handoffs select their owning home explicitly.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

KEY_FD_ENV = "HERMES_VAULT_KEY_FD"


def prepare_child_key(home: Path | str | None = None) -> tuple[int, dict[str, str]] | None:
    """One selected home's unlocked owner, never every key cached by a local process.

    The caller passes ``read_fd`` in ``pass_fds`` and closes it after spawn,
    including on failure. An omitted home resolves the current profile at call time.
    """
    from hermes_security.vault import is_unlocked, export_unlocked_key
    from hermes_security.session_authority import _vault_home
    if home is None:
        from hermes_constants import get_hermes_home
        home = get_hermes_home()
    owner = _vault_home(Path(home).expanduser().resolve())
    if owner is None or not is_unlocked(owner):
        return None
    keys = [{"home": str(owner), "key": base64.b64encode(export_unlocked_key(owner)).decode("ascii")}]
    payload = json.dumps(keys).encode("utf-8")
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, payload)  # a few hundred bytes: one write into an empty pipe never blocks
    finally:
        os.close(write_fd)
    os.set_inheritable(read_fd, True)
    return read_fd, {KEY_FD_ENV: str(read_fd)}


def adopt_inherited_key() -> bool:
    """Consume a parent's handed-over key if one was passed (idempotent: the fd and env var are gone after
    the first call). True when a vault was unlocked from it."""
    raw_fd = os.environ.pop(KEY_FD_ENV, "")
    if not raw_fd.isdigit():
        from hermes_security.session_authority import adopt_session_authority
        return adopt_session_authority()
    fd = int(raw_fd)
    try:
        chunks = []
        while chunk := os.read(fd, 4096):
            chunks.append(chunk)
    except OSError:
        return False
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    from hermes_security.vault import adopt_unlocked_key

    for entry in json.loads(b"".join(chunks).decode("utf-8")):
        adopt_unlocked_key(entry["home"], base64.b64decode(entry["key"]))
    return True
