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
"""

from hermes_security.errors import (
    PlaintextStateError,
    VaultError,
    VaultIntegrityError,
    VaultLockedError,
    VaultNotInitializedError,
    WrongMasterPasswordError,
)
from hermes_security.vault import (
    Vault,
    clear_vault_cache,
    get_vault,
    is_unlocked,
    lock_now,
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
]
