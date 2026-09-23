"""Transparent envelope I/O for Hermes-owned files.

Every read/write chokepoint routes through here.  Rules:

- A file inside a home with vault metadata is an envelope on disk; reads
  decrypt, writes encrypt.  Plaintext never touches the disk.
- No vault anywhere: reads pass through (pre-vault flows must keep working);
  writes inside the active Hermes home are REFUSED (fail-closed) — the write
  path is the enforcement point.
- Reading a vaulted file that is NOT an envelope is a PlaintextStateError
  (someone dropped a plaintext file into a vaulted home).

Import discipline: the vault probe is metadata-only (no crypto imports) so
this module is safe to import in crypto-free processes (update dispatch).

Text helpers mirror Path.read_text/write_text; bytes helpers are the
primitives.  Purpose labels are fixed per logical file kind so ciphertext
cannot be swapped between kinds (AAD binding).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from hermes_security.errors import PlaintextStateError

_META_FILENAME = ".hermes-vault"
_MAGIC = b"HRMVAULT\x00"


def _vault():
    """Deferred import: hermes_security.vault pulls the crypto stack."""

    from hermes_security import vault

    return vault


def _home_for(path: Path | str) -> Optional[Path]:
    """Nearest vaulted ancestor of *path*, else None. Metadata-only check."""

    candidate = Path(path).expanduser().resolve(strict=False)
    if candidate.is_file() or candidate.suffix:
        candidate = candidate.parent
    for directory in (candidate, *candidate.parents):
        if (directory / _META_FILENAME).is_file():
            return directory
    return None


def read_bytes(path: Path | str, *, purpose: str) -> Optional[bytes]:
    """Envelope-aware read. None when the file does not exist."""

    target = Path(path).expanduser()
    home = _home_for(target)
    try:
        # builtins.open (not Path.read_bytes): callers' patch seams for
        # denied/unreadable reads (config fail-closed guard) keep working.
        with open(target, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return None
    except OSError:
        raise
    if home is not None:
        if not raw.startswith(_MAGIC):
            raise PlaintextStateError(
                f"{target} is plaintext inside a vaulted home ({home}); "
                "refusing to read unprotected state"
            )
        return _vault().get_vault(home).decrypt(
            raw, purpose=purpose, relpath=_rel(target, home)
        )
    # No vault anywhere: plaintext reads pass through (the write path is the
    # enforcement point; reading existing plaintext never crashes a pre-vault
    # or vault-less flow).
    return raw


def write_bytes(path: Path | str, data: bytes, *, purpose: str) -> Path:
    """Envelope-aware write: encrypts when inside a vaulted home.

    Refuses to write plaintext when the target is inside the active Hermes
    home but no vault exists there (run ``hermes secure-vault migrate``).
    """

    target = Path(path).expanduser()
    home = _home_for(target)
    if home is None:
        from hermes_constants import get_hermes_home

        active = Path(get_hermes_home()).expanduser().resolve(strict=False)
        resolved = target.resolve(strict=False)
        if resolved == active or active in resolved.parents:
            raise PlaintextStateError(
                f"{target} is inside the Hermes home but no vault is initialized; "
                "run 'hermes secure-vault migrate' first"
            )
        # Outside the Hermes home entirely (temp files, exports): pass through.
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target
    return _vault().get_vault(home).write_bytes(target, data, purpose=purpose)


def _rel(target: Path, home: Path) -> str:
    try:
        return target.resolve(strict=False).relative_to(home).as_posix()
    except ValueError:
        return target.name


def read_text(path: Path | str, *, purpose: str, encoding: str = "utf-8") -> Optional[str]:
    data = read_bytes(path, purpose=purpose)
    return None if data is None else data.decode(encoding)


def write_text(path: Path | str, text: str, *, purpose: str, encoding: str = "utf-8") -> Path:
    return write_bytes(path, text.encode(encoding), purpose=purpose)


def read_json(path: Path | str, *, purpose: str) -> Optional[Any]:
    data = read_bytes(path, purpose=purpose)
    return None if data is None else json.loads(data.decode("utf-8-sig"))


def write_json(path: Path | str, payload: Any, *, purpose: str) -> Path:
    return write_bytes(
        path, json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"), purpose=purpose
    )


def looks_like_envelope(path: Path | str) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(len(_MAGIC)) == _MAGIC
    except OSError:
        return False
