"""Vault error taxonomy. Security failures stay fatal - never fall back."""

from __future__ import annotations


class VaultError(RuntimeError):
    """Base class for mandatory-vault failures."""


class VaultNotInitializedError(VaultError):
    """No vault metadata exists for this home; run ``hermes vault init``."""


class VaultLockedError(VaultError):
    """Vault exists but is locked: no master password has been supplied."""


class WrongMasterPasswordError(VaultError):
    """The supplied master password does not open this vault."""


class PlaintextStateError(VaultError):
    """Existing state would require an unsafe implicit plaintext migration."""


class VaultIntegrityError(VaultError):
    """Ciphertext was damaged, substituted, or opened for the wrong purpose."""


class VaultKeyMismatchError(VaultError):
    """A SQLCipher database did not accept this process's vault-derived key.

    Wrong key, not a damaged file: the caller's corruption probes must not
    quarantine or back up the database on this class. Distinct from
    :class:`WrongMasterPasswordError` (the vault slot itself refused the
    credential) — this is the per-database key derived from an unlocked vault
    that does not own the target database (e.g. a profile home's vault opening
    the shared default-root board)."""
