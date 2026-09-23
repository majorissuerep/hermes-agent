"""Transparent envelope I/O for Hermes-owned files.

Every read/write chokepoint routes through here.  Rules:

- A file inside a home with vault metadata is an envelope on disk; reads
  decrypt, writes encrypt.  Plaintext never touches the disk.
- No vault metadata in any ancestor AND the file is inside the active
  Hermes home: writing is refused (fail-closed) — run ``hermes vault init``.
  Reading a missing file returns None / {} as before.
- Reading a vaulted file that is NOT an envelope is a PlaintextStateError
  (someone dropped a plaintext file into a vaulted home).

Text helpers mirror Path.read_text/write_text; bytes helpers are the
primitives.  Purpose labels are fixed per logical file kind so ciphertext
cannot be swapped between kinds (AAD binding).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from hermes_security.errors import PlaintextStateError, VaultIntegrityError
from hermes_security.vault import (
    _META_FILENAME,
    find_vault_home,
    get_vault,
    vault_exists,
)

_MAGIC = b"HRMVAULT\x00"


def _home_for(path: Path | str) -> Optional[Path]:
    """Nearest vaulted ancestor of *path*, else None."""

    candidate = Path(path).expanduser().resolve(strict=False)
    for directory in (candidate, *candidate.parents):
        if (directory / _META_FILENAME).is_file():
            return directory
    return None


def read_bytes(path: Path | str, *, purpose: str) -> Optional[bytes]:
    """Envelope-aware read. None when the file does not exist."""

    target = Path(path).expanduser()
    home = _home_for(target)
    try:
        raw = target.read_bytes()
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
        vault = get_vault(home)
        return vault.decrypt(raw, purpose=purpose, relpath=_rel(target, home))
    # No vaulted ancestor. Refuse plaintext reads inside the active Hermes
    # home too — a stray plaintext file must never be consumed silently.
    from hermes_constants import get_hermes_home

    active = Path(get_hermes_home()).expanduser().resolve(strict=False)
    resolved = target.resolve(strict=False)
    if resolved == active or active in resolved.parents:
        raise PlaintextStateError(
            f"{target} is plaintext and no vault is initialized at {active}; "
            "run 'hermes vault init' and migrate secrets deliberately"
        )
    return raw


def write_bytes(path: Path | str, data: bytes, *, purpose: str) -> Path:
    """Envelope-aware write: encrypts when inside a vaulted home.

    Refuses to write plaintext when the target is inside the active Hermes
    home but no vault exists there (fresh install: run ``hermes vault init``).
    """

    target = Path(path).expanduser()
    home = _home_for(target)
    if home is None:
        from hermes_constants import get_hermes_home

        active = Path(get_hermes_home()).expanduser().resolve(strict=False)
        resolved = target.resolve(strict=False)
        if resolved == active or active in resolved.parents or resolved in (active,):
            raise PlaintextStateError(
                f"{target} is inside the Hermes home but no vault is initialized; "
                "run 'hermes vault init' first"
            )
        # Outside the Hermes home entirely (temp files, exports): pass through.
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target
    vault = get_vault(home)
    return vault.write_bytes(target, data, purpose=purpose)


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
