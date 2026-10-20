"""Passphrase slots wrap the existing data master; encrypted payloads remain v1."""
from __future__ import annotations

import base64
import hmac
import json
import secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from hermes_security.errors import VaultIntegrityError, WrongMasterPasswordError

_METADATA_VERSION = 2
_WRAP_AAD = b"hermes-vault-passphrase-wrap-v2|"


def _decode(value, size=None):
    try:
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError("invalid encoded value")
        raw = base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
        if size is not None and len(raw) != size:
            raise ValueError("invalid length")
        return raw
    except (ValueError, UnicodeError) as exc:
        raise VaultIntegrityError("Invalid vault credential encoding") from exc


def _canonical(meta):
    return json.dumps(meta, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _kdf(slot):
    from hermes_security import vault

    n, r, p = (slot.get(name) for name in ("kdf_n", "kdf_r", "kdf_p"))
    if (slot.get("kdf") != "scrypt" or any(type(value) is not int for value in (n, r, p))
            or not 2 <= n <= 2 ** 18 or n & (n - 1) or not 1 <= r <= 16 or not 1 <= p <= 4
            or 128 * n * r > 256 * 1024 ** 2 or n * r * p > 2 ** 22):
        raise VaultIntegrityError("Unsupported or excessive vault KDF parameters")
    return _decode(slot.get("salt"), vault._SALT_BYTES), n, r, p


def _proof(meta, key):
    from hermes_security import vault

    salt, verifier = _decode(meta.get("salt"), 32), _decode(meta.get("verifier"))
    if len(key) != 32 or len(verifier) != 12 + len(vault._VERIFIER_PLAINTEXT) + 16:
        raise VaultIntegrityError("Invalid vault master-key proof")
    if not vault._check_verifier(key, salt, verifier):
        raise WrongMasterPasswordError("The credential does not open this vault")


def authenticate_metadata(meta, key):
    from hermes_security import vault

    result = {name: value for name, value in meta.items() if name != "authentication"}
    if result.get("version") == _METADATA_VERSION:
        tag = hmac.digest(vault.Vault(Path("."), key).derive_key("metadata-auth"), _canonical(result), "sha256")
        result["authentication"] = vault._b64e(tag)
    return result


def verify_master_key(meta, key):
    if not isinstance(meta, dict) or meta.get("version") not in (1, _METADATA_VERSION):
        raise VaultIntegrityError("Unsupported vault metadata version")
    _proof(meta, key)
    if meta["version"] == _METADATA_VERSION:
        slot = meta.get("passphrase")
        if not isinstance(slot, dict):
            raise VaultIntegrityError("Missing passphrase slot")
        _kdf(slot)
        _decode(slot.get("nonce"), 12)
        _decode(slot.get("sealed"), 48)
        expected = authenticate_metadata(meta, key)["authentication"]
        if not hmac.compare_digest(_decode(meta.get("authentication"), 32), _decode(expected, 32)):
            raise VaultIntegrityError("Vault metadata authentication failed")
    elif "passphrase" in meta or "authentication" in meta:
        raise VaultIntegrityError("Vault metadata downgrade refused")


def _aad(meta, slot):
    params = {name: value for name, value in slot.items() if name not in ("sealed", "nonce")}
    return _WRAP_AAD + _canonical({"version": 2, "salt": meta.get("salt"), "verifier": meta.get("verifier"),
                                   "passphrase": params})


def unlock_master_key(meta, password):
    from hermes_security import vault

    if not isinstance(meta, dict) or meta.get("version") not in (1, 2):
        raise VaultIntegrityError("Unsupported vault metadata version")
    slot = meta if meta["version"] == 1 else meta.get("passphrase")
    if not isinstance(slot, dict):
        raise VaultIntegrityError("Missing passphrase slot")
    salt, n, r, p = _kdf(slot)
    derived = vault._derive_root_key(password, salt, n, r, p)
    if meta["version"] == 1:
        key = derived
    else:
        try:
            key = AESGCM(derived).decrypt(_decode(slot.get("nonce"), 12),
                                          _decode(slot.get("sealed"), 48), _aad(meta, slot))
        except InvalidTag as exc:
            raise WrongMasterPasswordError("The master password does not open this vault") from exc
    verify_master_key(meta, key)
    return key


def change_passphrase(home: Path | str, new_password: str | bytes) -> None:
    """Rewrap an already-unlocked exact owner; data, child grants and key slots stay valid.

    Historical metadata/backups and already-known keys cannot be revoked by changing a password.
    """
    from hermes_security import vault
    from hermes_security.vault_metadata import exact_home, writer

    if not isinstance(new_password, (str, bytes)) or not new_password:
        raise ValueError("A non-empty new passphrase is required")
    home = exact_home(home)
    unlocked = vault.get_vault(home, allow_env_unlock=False)
    with writer(home, unlocked) as transaction:
        meta = dict(transaction.meta)
        if meta["version"] == 1:
            # Retaining the original KDF salt would let the retired password still derive the data key.
            salt = secrets.token_bytes(32)
            meta = {name: value for name, value in meta.items() if not name.startswith("kdf")}
            meta.update(salt=vault._b64e(salt), verifier=vault._b64e(vault._make_verifier(unlocked.master_key, salt)))
        meta["version"] = _METADATA_VERSION
        slot = {"kdf": "scrypt", "kdf_n": vault._SCRYPT_N, "kdf_r": vault._SCRYPT_R,
                "kdf_p": vault._SCRYPT_P, "salt": vault._b64e(secrets.token_bytes(32))}
        salt, n, r, p = _kdf(slot)
        wrapping_key = vault._derive_root_key(new_password, salt, n, r, p)
        nonce = secrets.token_bytes(12)
        slot.update(nonce=vault._b64e(nonce), sealed=vault._b64e(
            AESGCM(wrapping_key).encrypt(nonce, unlocked.master_key, _aad(meta, slot))))
        meta["passphrase"] = slot
        transaction.commit(meta)
