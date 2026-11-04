"""Explicit offline conversion of the older delegation cache formats."""
from __future__ import annotations

import json

from hermes_security import frames
from hermes_security.artifact_paths import is_delegation_manifest_path
from hermes_security.errors import VaultIntegrityError
from hermes_security.vault import _atomic_write


def repair_delegation_state(vault, path, relative, report):
    """Runtime readers never invoke this older-purpose/plaintext import path."""
    manifest = is_delegation_manifest_path(relative)
    purpose = "delegation-manifest:v1" if manifest else "state"
    raw = path.read_bytes()
    if raw.startswith(b"HRMVAULT\x00"):
        try:
            vault.decrypt(raw, purpose=purpose, relpath=relative.as_posix())
            return
        except VaultIntegrityError:
            if not manifest:
                raise
            content = vault.decrypt(raw, purpose="state", relpath=relative.as_posix())
    else:
        try:
            content = raw.decode("utf-8").encode("utf-8")
            if b"\x00" in content:
                raise UnicodeError("binary stream")
        except UnicodeError:
            if manifest:
                raise VaultIntegrityError("Delegation control manifest is not JSON") from None
            payloads, consumed = frames.split_stream(raw, vault=vault, purpose="log", strict=True)
            if consumed != len(raw):
                raise VaultIntegrityError("Incomplete legacy delegation stream")
            content = b"".join(payloads)
    if manifest and not isinstance(json.loads(content), dict):
        raise VaultIntegrityError("Delegation manifest must be an object")
    encoded = vault.encrypt(content, purpose=purpose, relpath=relative.as_posix())
    if vault.decrypt(encoded, purpose=purpose, relpath=relative.as_posix()) != content:
        raise VaultIntegrityError("Delegation repair roundtrip mismatch")
    _atomic_write(path, encoded)
    report["sealed"].append(relative.as_posix())
