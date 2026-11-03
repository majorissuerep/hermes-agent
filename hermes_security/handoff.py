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
import select
import stat
import time
from pathlib import Path

KEY_FD_ENV = "HERMES_VAULT_KEY_FD"


def prepare_child_key(home: Path | str | None = None, *,
                      additional_homes: tuple[Path, ...] = (),
                      retain_session: bool = True) -> tuple[int, dict[str, str]] | None:
    """Selected owners only, never every key cached by a local process.

    The caller passes ``read_fd`` in ``pass_fds`` and closes it after spawn,
    including on failure. An omitted home resolves the current profile at call time.
    A maintenance caller must name every additional home it needs explicitly.
    """
    from hermes_security.vault import is_unlocked, export_unlocked_key
    from hermes_security.errors import VaultError, VaultLockedError
    from hermes_security.session_authority import _vault_home, inherited_grant, validate_inherited_authority
    if home is None:
        from hermes_constants import get_hermes_home
        home = get_hermes_home()
    owner = _vault_home(Path(home).expanduser().resolve())
    if owner is None or not is_unlocked(owner):
        return None
    owners = {owner}
    for selected in additional_homes:
        extra = _vault_home(Path(selected).expanduser().resolve())
        if extra is None or not is_unlocked(extra):
            raise VaultLockedError("Every named maintenance home must already be unlocked.")
        owners.add(extra)
    for selected in owners:
        validate_inherited_authority(selected)
    # Only finite native dispatchers transferring ownership to their real worker
    # may detach their temporary launch session. Terminal-owned children retain it.
    keys = []
    for selected in sorted(owners):
        profile = Path(home).resolve() if selected == owner else selected
        grant = inherited_grant(profile) if retain_session else None
        if grant is not None:
            profile = Path(json.loads(grant)["home"])
        entry = {"home": str(selected), "key": base64.b64encode(export_unlocked_key(selected)).decode("ascii"),
                 "profile": str(profile)}
        if grant is not None or not retain_session:
            entry["grant"] = grant
        keys.append(entry)
    payload = json.dumps(keys).encode("utf-8")
    if len(payload) > 4096:
        raise VaultError("The named maintenance owners exceed the one-shot pipe limit.")
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, payload)  # bounded by PIPE_BUF: one write into an empty pipe never blocks
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
    if fd < 3:
        from hermes_security.errors import VaultError
        raise VaultError("The vault key handoff must be distinct from terminal input/output.")
    try:
        if not (stat.S_ISFIFO(os.fstat(fd).st_mode) or stat.S_ISSOCK(os.fstat(fd).st_mode)):
            from hermes_security.errors import VaultError
            raise VaultError("The vault key handoff requires a one-shot kernel descriptor.")
        chunks = []
        size = 0
        deadline = time.monotonic() + 5
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], max(0, remaining))[0]:
                from hermes_security.errors import VaultError
                raise VaultError("The vault key handoff did not reach EOF before its deadline.")
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            size += len(chunk)
            if size > 4096:
                from hermes_security.errors import VaultError
                raise VaultError("The vault key handoff exceeds its bounded frame.")
            chunks.append(chunk)
    except OSError:
        return False
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    from hermes_security.vault import adopt_unlocked_key, is_unlocked, lock_now
    from hermes_security.session_authority import record_inherited_authority

    for entry in json.loads(b"".join(chunks).decode("utf-8")):
        # Frozen updater frames may omit provenance; their inherited environment
        # is still checked. New frames retain provenance when the env is scrubbed.
        raw = entry.get("grant") if "grant" in entry else os.environ.get("HERMES_SESSION_VAULT")
        profile = Path(entry.get("profile", entry["home"]))
        from hermes_security.session_authority import _vault_home
        if "profile" not in entry and raw:
            legacy_profile = Path(json.loads(raw)["home"])
            if _vault_home(legacy_profile) == Path(entry["home"]).resolve():
                profile = legacy_profile
            else:
                raw = None  # frozen multi-home maintenance frames have one selected grant
        if _vault_home(profile.resolve()) != Path(entry["home"]).resolve():
            from hermes_security.errors import VaultError
            raise VaultError("The inherited key does not belong to its named profile.")
        key = base64.b64decode(entry["key"], validate=True)
        was_unlocked = is_unlocked(entry["home"])
        adopt_unlocked_key(entry["home"], key)
        try:
            record_inherited_authority(profile, raw, trusted_key=key)
        except BaseException:
            if not was_unlocked:
                lock_now(entry["home"])
            raise
    return True
