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
