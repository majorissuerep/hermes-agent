"""Small encrypted migration checkpoint, atomically carried by credential metadata."""
from __future__ import annotations

import json
from pathlib import Path

from hermes_security.errors import VaultIntegrityError

_FIELD = "migration_pending"
_PURPOSE = "migration-checkpoint:v1"
_RELATIVE = ".hermes-vault.migration"
_MAX_STATE = 64 * 1024


def seal(vault, state: dict) -> str:
    from hermes_security.vault import _b64e

    raw = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > _MAX_STATE:
        raise VaultIntegrityError("Migration checkpoint exceeds its bounded size")
    return _b64e(vault.encrypt(raw, purpose=_PURPOSE, relpath=_RELATIVE))


def initial(home: Path, identity: str) -> dict:
    return {"version": 1, "home": str(home), "id": identity, "phase": "preparing",
            "backup": None, "sha256": None, "current": None}


def pending(home: Path) -> bool:
    from hermes_security.vault import _load_meta

    meta = _load_meta(home)
    return meta is not None and _FIELD in meta


def read(home: Path, vault) -> dict:
    from hermes_security.vault import _load_meta

    return decode(_load_meta(home), home, vault)


def decode(meta, home: Path, vault) -> dict:
    from hermes_security.vault import _b64d

    encoded = meta.get(_FIELD) if meta else None
    if not isinstance(encoded, str) or len(encoded) > 2 * _MAX_STATE:
        raise VaultIntegrityError("No authenticated migration checkpoint; existing vault was left unchanged")
    try:
        raw = vault.decrypt(_b64d(encoded), purpose=_PURPOSE, relpath=_RELATIVE)
        state = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise VaultIntegrityError("Migration checkpoint is damaged; preserve the originals and rollback") from exc
    if (not isinstance(state, dict) or state.get("version") != 1 or state.get("home") != str(home)
            or state.get("phase") not in {"preparing", "converting"}
            or not isinstance(state.get("id"), str) or len(state["id"]) != 32):
        raise VaultIntegrityError("Migration checkpoint does not belong to this exact home")
    return state


def write(home: Path, vault, state: dict | None) -> None:
    from hermes_security.vault_metadata import writer

    with writer(home, vault) as transaction:
        updated = dict(transaction.meta)
        if state is None:
            updated.pop(_FIELD, None)
        else:
            updated[_FIELD] = seal(vault, state)
        transaction.commit(updated)
