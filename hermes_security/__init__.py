"""Mandatory at-rest encryption for every Hermes-owned file and database.

Design contract:

- The user supplies a master password.  scrypt(password, per-home random salt)
  derives the 256-bit root key; the password and the key are never written
  anywhere.  Losing the password loses the data - by design.
- Every purpose (config file, .env, auth store, SQLCipher database, log frame)
  derives its own key from the root key via HKDF-SHA-256 domain separation.
- Files are AES-256-GCM envelopes whose AAD binds the logical purpose AND the
  path relative to the home, so ciphertext cannot be swapped between files.
- SQLite databases are SQLCipher databases; standard sqlite3 cannot open them.
- Append-only streams (logs, transcripts) use per-record AES-GCM frames.
- Plaintext state where encrypted state is required is a hard, fail-closed
  error.  There is no implicit migration and no fallback to plaintext.

Import discipline: importing ANY submodule of this package must not drag the
crypto stack (``cryptography``) into processes that never touch a vault —
notably update dispatch, whose native crypto lib locks on Windows
self-update.  ``errors`` and the lazy attribute imports below keep the
package import cheap; the heavy modules (vault → cryptography, sqlite →
sqlcipher3) load only when actually used.
"""

from __future__ import annotations

from typing import Any

from hermes_security.errors import (
    PlaintextStateError,
    VaultError,
    VaultIntegrityError,
    VaultLockedError,
    VaultNotInitializedError,
    WrongMasterPasswordError,
)

__all__ = [
    "Vault",
    "VaultError",
    "VaultIntegrityError",
    "VaultLockedError",
    "VaultNotInitializedError",
    "WrongMasterPasswordError",
    "PlaintextStateError",
    "get_vault",
    "clear_vault_cache",
    "is_unlocked",
    "lock_now",
    "vault_status",
    "count_encrypted_files",
]

_LAZY = {
    "Vault": ("hermes_security.vault", "Vault"),
    "get_vault": ("hermes_security.vault", "get_vault"),
    "clear_vault_cache": ("hermes_security.vault", "clear_vault_cache"),
    "is_unlocked": ("hermes_security.vault", "is_unlocked"),
    "lock_now": ("hermes_security.vault", "lock_now"),
    "vault_status": ("hermes_security.vault", "vault_status"),
    "count_encrypted_files": ("hermes_security.vault", "count_encrypted_files"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attr = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    import importlib

    return getattr(importlib.import_module(module_name), attr)
