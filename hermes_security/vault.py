"""Master-password vault: scrypt root key, HKDF domain separation, AES-GCM envelopes.

The root key never touches disk.  The home contains only a public metadata
file (KDF parameters + random salt + password verifier).  The verifier lets
us reject a wrong password at unlock time before any file is decrypted.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from hermes_security.errors import (
    PlaintextStateError,
    VaultError,
    VaultIntegrityError,
    VaultLockedError,
    VaultNotInitializedError,
    WrongMasterPasswordError,
)
_MAGIC = b"HRMVAULT\x00"
_VERSION = 1
_NONCE_BYTES = 12
_ROOT_KEY_BYTES = 32
_SALT_BYTES = 32
_META_FILENAME = ".hermes-vault"
_VERIFIER_PLAINTEXT = b"hermes-vault-v1-verifier"
_KEY_SLOT_AAD = b"hermes-vault-keyslot-v1"

# scrypt parameters for new vaults.  Stored per-vault in metadata so future
# releases can tighten them without invalidating existing vaults.
_SCRYPT_N = 2 ** 15
_SCRYPT_R = 8
_SCRYPT_P = 1


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text.encode("ascii"))


def _derive_root_key(password: str | bytes, salt: bytes, n: int, r: int, p: int) -> bytes:
    kdf = Scrypt(salt=salt, length=_ROOT_KEY_BYTES, n=n, r=r, p=p)
    pw = password.encode("utf-8") if isinstance(password, str) else password
    return kdf.derive(pw)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    """Write *data* to *path* atomically: temp file + fsync + rename + dir fsync."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def _load_meta(home: Path) -> Optional[dict[str, Any]]:
    meta_path = home / _META_FILENAME
    try:
        raw = meta_path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        meta = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VaultIntegrityError(f"Vault metadata at {meta_path} is corrupt") from exc
    if not isinstance(meta, dict):
        raise VaultIntegrityError(f"Vault metadata at {meta_path} is corrupt")
    return meta


class Vault:
    """An unlocked vault for one Hermes home.  Plaintext exists only in this process."""

    def __init__(self, home: Path, master_key: bytes) -> None:
        self.home = Path(home).expanduser().resolve()
        self.master_key = master_key

    # -- key derivation ----------------------------------------------------

    def derive_key(self, purpose: str) -> bytes:
        if not purpose or "\x00" in purpose:
            raise ValueError("Vault purpose must be a non-empty text label")
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=("hermes/vault/v1/" + purpose).encode("utf-8"),
        ).derive(self.master_key)

    # -- envelopes ----------------------------------------------------------

    def _aad(self, purpose: str, relpath: str) -> bytes:
        return f"hermes-vault|{_VERSION}|{purpose}|{relpath}".encode("utf-8")

    def encrypt(self, plaintext: bytes, *, purpose: str, relpath: str) -> bytes:
        nonce = secrets.token_bytes(_NONCE_BYTES)
        ciphertext = AESGCM(self.derive_key("file:" + purpose)).encrypt(
            nonce, plaintext, self._aad(purpose, relpath)
        )
        return _MAGIC + bytes((_VERSION,)) + nonce + ciphertext

    def decrypt(self, envelope: bytes, *, purpose: str, relpath: str) -> bytes:
        prefix_size = len(_MAGIC) + 1 + _NONCE_BYTES
        if len(envelope) < prefix_size + 16 or not envelope.startswith(_MAGIC):
            raise VaultIntegrityError("File is not a Hermes encrypted envelope")
        version = envelope[len(_MAGIC)]
        if version != _VERSION:
            raise VaultIntegrityError(f"Unsupported vault format version: {version}")
        nonce_start = len(_MAGIC) + 1
        nonce = envelope[nonce_start : nonce_start + _NONCE_BYTES]
        ciphertext = envelope[nonce_start + _NONCE_BYTES :]
        try:
            return AESGCM(self.derive_key("file:" + purpose)).decrypt(
                nonce, ciphertext, self._aad(purpose, relpath)
            )
        except InvalidTag as exc:
            raise VaultIntegrityError(
                f"Vault authentication failed for {relpath!r} (purpose {purpose!r})"
            ) from exc

    # -- path policy ---------------------------------------------------------

    def _resolve(self, path: Path | str) -> tuple[Path, str]:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.home / candidate
        candidate = candidate.resolve(strict=False)
        try:
            rel = candidate.relative_to(self.home)
        except ValueError as exc:
            raise ValueError(
                f"Encrypted Hermes files must stay inside the Hermes home ({self.home})"
            ) from exc
        return candidate, rel.as_posix()

    def write_bytes(self, path: Path | str, plaintext: bytes, *, purpose: str) -> Path:
        target, rel = self._resolve(path)
        envelope = self.encrypt(plaintext, purpose=purpose, relpath=rel)
        _atomic_write(target, envelope)
        return target

    def read_bytes(self, path: Path | str, *, purpose: str) -> bytes:
        target, rel = self._resolve(path)
        try:
            envelope = target.read_bytes()
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise VaultIntegrityError(f"Cannot read encrypted file at {target}") from exc
        return self.decrypt(envelope, purpose=purpose, relpath=rel)


# -- process-level vault registry -----------------------------------------

_VAULTS: dict[Path, Vault] = {}
_VAULT_LOCK = threading.RLock()


def find_vault_home(path: Path | str) -> Path:
    """Walk up from *path* to the nearest directory holding vault metadata.

    Files can live anywhere under the home (logs/, sessions/, profiles/...),
    so the vault for a file is the first ancestor with ``.hermes-vault``.
    Falls back to the active Hermes home when no ancestor carries metadata.
    """

    candidate = Path(path).expanduser().resolve(strict=False)
    if candidate.is_file() or candidate.suffix:
        candidate = candidate.parent
    for directory in (candidate, *candidate.parents):
        if (directory / _META_FILENAME).is_file():
            return directory
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home()).resolve()


def vault_meta_path(home: Path | str) -> Path:
    return Path(home).expanduser() / _META_FILENAME


def vault_exists(home: Path | str) -> bool:
    """True when vault metadata exists. EACCES must NOT read as "no vault":
    on 3.13+ Path.exists() swallows permission errors, which would let the
    CLI treat an unreadable home as uninitialized and offer to MIGRATE it.
    Fail closed by raising — the caller surfaces the real error."""

    meta = vault_meta_path(home)
    try:
        meta.stat()
        return True
    except FileNotFoundError:
        return False


# Entries that are CODE, not user state: the git checkout of the app itself,
# its virtualenvs, and the uv tool cache that live inside the home on git
# installs. They contain no user content (bytes are public), so they neither
# block vault init nor get encrypted. Everything else IS user state.
_CODE_ENTRIES = frozenset(
    {"hermes-agent", "uv", "venv", ".venv"}
)

# The deterministic first-run scaffold (ensure_hermes_home): known empty
# directories plus the stock SOUL.md persona template. Public boilerplate,
# never user content — the vault re-seals SOUL.md on first write.
_SCAFFOLD_MARKER_FILES = frozenset({".last_prune"})
_SCAFFOLD_DIRS = frozenset(
    {
        "cron", "sessions", "logs", "logs/curator", "memories", "pairing",
        "hooks", "image_cache", "audio_cache", "skills", "cache",
        "cache/scratch", "state-snapshots",
    }
)

# Same as migrate._SKIP_DIRS — code/artifact trees with no user content.
# Duplicated here so vault_status is self-contained (no cross-submodule import
# beyond the lazy vault probe).
_SKIP_DIRS = frozenset(
    {
        "hermes-agent", "uv", "venv", ".venv", "node_modules", "__pycache__",
        "skills", "optional-skills", "plugins", "pets", "skins", "tui-widgets",
        "desktop-plugins", ".git", "graphify", "lsp", "bin", "cache",
        "image_cache", "audio_cache",
    }
)
_SCAFFOLD_FILES = frozenset({"SOUL.md"})


def _has_user_state(directory: Path) -> bool:
    for item in directory.iterdir():
        if item.name in _CODE_ENTRIES:
            continue
        if item.is_dir():
            if _dir_is_scaffold(item):
                continue
            return True
        if item.is_file() and item.name in _SCAFFOLD_FILES:
            continue
        return True
    return False


def _dir_is_scaffold(directory: Path) -> bool:
    """A scaffold directory holds no user content: only known marker files
    (`.last_prune` prune stamps) or nothing at all."""

    for path in directory.rglob("*"):
        if path.is_file() and path.name not in _SCAFFOLD_MARKER_FILES:
            return False
    return True


def init_vault(
    home: Path | str,
    password: str | bytes,
    *,
    _allow_existing_state: bool = False,
) -> None:
    """Create vault metadata for *home*.

    Refuses to overlay existing USER state unless ``_allow_existing_state`` is
    set (used by the migration path, which converts that state in place).
    """

    home_path = Path(home).expanduser().resolve()
    if vault_exists(home_path):
        raise VaultError(f"A vault already exists at {home_path}")
    if home_path.exists() and not _allow_existing_state and _has_user_state(home_path):
        names = ", ".join(sorted(item.name for item in home_path.iterdir())[:10])
        raise PlaintextStateError(
            "Refusing to initialize a vault over pre-existing state: " + names
        )
    home_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(home_path, 0o700)

    salt = secrets.token_bytes(_SALT_BYTES)
    root_key = _derive_root_key(password, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
    meta = {
        "version": _VERSION,
        "kdf": "scrypt",
        "kdf_n": _SCRYPT_N,
        "kdf_r": _SCRYPT_R,
        "kdf_p": _SCRYPT_P,
        "salt": _b64e(salt),
        "verifier": _b64e(_make_verifier(root_key, salt)),
    }
    _atomic_write(home_path / _META_FILENAME, json.dumps(meta).encode("utf-8"))


def _make_verifier(root_key: bytes, salt: bytes) -> bytes:
    verifier_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"hermes/vault/v1/verifier",
    ).derive(root_key)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    return nonce + AESGCM(verifier_key).encrypt(nonce, _VERIFIER_PLAINTEXT, b"hermes-vault-verifier")


def _check_verifier(root_key: bytes, salt: bytes, verifier: bytes) -> bool:
    verifier_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"hermes/vault/v1/verifier",
    ).derive(root_key)
    nonce, ciphertext = verifier[:_NONCE_BYTES], verifier[_NONCE_BYTES:]
    try:
        plain = AESGCM(verifier_key).decrypt(nonce, ciphertext, b"hermes-vault-verifier")
    except InvalidTag:
        return False
    return plain == _VERIFIER_PLAINTEXT


# ---------------------------------------------------------------------------
# Key slots (v2 metadata): LUKS-style multiple credentials wrap ONE master
# key. The system stores ONLY public material on disk (salt + verifier, or
# an X25519 PUBLIC key + the master key sealed to it). The credential —
# passphrase or private key — lives wherever the HUMAN wants it. Either
# slot unlocks the same in-memory master key; no file is ever re-encrypted
# when slots are added, removed, or rotated.
# ---------------------------------------------------------------------------

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PrivateFormat,
    PublicFormat,
)
from cryptography.hazmat.primitives.serialization import NoEncryption


def generate_keypair() -> tuple[bytes, bytes]:
    """Return (private_key_raw32, public_key_raw32). The private half is
    shown to the human ONCE by the CLI and never stored by the system."""

    private = X25519PrivateKey.generate()
    pub = private.public_key()
    return (
        private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()),
        pub.public_bytes(Encoding.Raw, PublicFormat.Raw),
    )


def _seal_to_public_key(public_key_raw: bytes, master_key: bytes) -> dict:
    """ECIES-style seal: ephemeral X25519 + HKDF + AES-GCM (age-style)."""

    eph = X25519PrivateKey.generate()
    shared = eph.exchange(X25519PublicKey.from_public_bytes(public_key_raw))
    wrap_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"hermes/vault/v1/keyslot-x25519",
    ).derive(shared)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    sealed = AESGCM(wrap_key).encrypt(nonce, master_key, _KEY_SLOT_AAD)
    eph_pub = eph.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return {
        "type": "x25519",
        "eph": _b64e(eph_pub),
        "nonce": _b64e(nonce),
        "sealed": _b64e(sealed),
    }


def _unseal_with_private_key(slot: dict, private_key_raw: bytes) -> bytes:
    eph_pub = X25519PublicKey.from_public_bytes(_b64d(slot["eph"]))
    shared = X25519PrivateKey.from_private_bytes(private_key_raw).exchange(eph_pub)
    wrap_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"hermes/vault/v1/keyslot-x25519",
    ).derive(shared)
    try:
        return AESGCM(wrap_key).decrypt(
            _b64d(slot["nonce"]), _b64d(slot["sealed"]), _KEY_SLOT_AAD
        )
    except InvalidTag as exc:
        raise WrongMasterPasswordError(
            "This private key does not open this vault"
        ) from exc


def add_key_slot(
    home: Path | str,
    *,
    public_key_raw: bytes | None = None,
    unlocked_vault: "Vault | None" = None,
    password: str | bytes | None = None,
) -> None:
    """Add an X25519 public-key slot to an existing vault.

    Requires EITHER an already-unlocked vault (same process) OR the current
    passphrase (to unlock, add the slot, re-lock). The master key on disk is
    only ever stored sealed to the new public key — the private half lives
    with the human.
    """

    home_path = Path(home).expanduser().resolve()
    meta = _load_meta(home_path)
    if meta is None:
        raise VaultNotInitializedError(f"No vault at {home_path}")

    vault = unlocked_vault
    if vault is None:
        if password is None:
            raise VaultLockedError("Unlock the vault (or pass the passphrase) to add a key slot")
        vault = unlock(home_path, password)
    if public_key_raw is None:
        raise ValueError("public_key_raw is required")

    slots = list(meta.get("key_slots") or [])
    slots.append(_seal_to_public_key(public_key_raw, vault.master_key))
    meta["key_slots"] = slots
    _atomic_write(home_path / _META_FILENAME, json.dumps(meta).encode("utf-8"))


def remove_key_slot(home: Path | str, *, index: int) -> None:
    """Drop key slot *index* (rotation: add new, remove old)."""
    home_path = Path(home).expanduser().resolve()
    meta = _load_meta(home_path)
    if meta is None:
        raise VaultNotInitializedError(f"No vault at {home_path}")
    slots = list(meta.get("key_slots") or [])
    if not 0 <= index < len(slots):
        raise VaultError(f"No key slot {index} (vault has {len(slots)})")
    slots.pop(index)
    meta["key_slots"] = slots
    _atomic_write(home_path / _META_FILENAME, json.dumps(meta).encode("utf-8"))


def unlock_with_private_key(home: Path | str, private_key_raw: bytes) -> Vault:
    """Unlock via any X25519 key slot: try each sealed slot, cache on success."""

    home_path = Path(home).expanduser().resolve()
    meta = _load_meta(home_path)
    if meta is None:
        raise VaultNotInitializedError(f"No vault at {home_path}")
    with _VAULT_LOCK:
        cached = _VAULTS.get(home_path)
        if cached is not None:
            return cached
    slots = meta.get("key_slots") or []
    key_slots = [s for s in slots if isinstance(s, dict) and s.get("type") == "x25519"]
    if not key_slots:
        raise VaultLockedError(
            f"The vault at {home_path} has no key slot; unlock with the master password"
        )
    last_error: Exception | None = None
    for slot in key_slots:
        try:
            master_key = _unseal_with_private_key(slot, private_key_raw)
        except WrongMasterPasswordError as exc:
            last_error = exc
            continue
        vault = Vault(home=home_path, master_key=master_key)
        with _VAULT_LOCK:
            _VAULTS[home_path] = vault
        return vault
    raise last_error or WrongMasterPasswordError(
        "This private key does not open any slot of this vault"
    )


def _zero(buf: bytearray | bytes) -> None:
    if isinstance(buf, bytearray):
        for i in range(len(buf)):
            buf[i] = 0


def unlock(home: Path | str, password: str | bytes) -> Vault:
    """Verify the master password and cache the unlocked vault for this process."""

    home_path = Path(home).expanduser().resolve()
    meta = _load_meta(home_path)
    if meta is None:
        raise VaultNotInitializedError(
            f"No vault at {home_path}; run 'hermes vault init' first"
        )
    with _VAULT_LOCK:
        cached = _VAULTS.get(home_path)
        if cached is not None:
            return cached

    salt = _b64d(meta.get("salt", ""))
    verifier = _b64d(meta.get("verifier", ""))
    if len(salt) != _SALT_BYTES or len(verifier) < _NONCE_BYTES + 16:
        raise VaultIntegrityError(f"Vault metadata at {home_path} is corrupt")
    n, r, p = (int(meta.get(k, d)) for k, d in (("kdf_n", _SCRYPT_N), ("kdf_r", _SCRYPT_R), ("kdf_p", _SCRYPT_P)))
    if meta.get("kdf") != "scrypt" or n <= 0 or r <= 0 or p <= 0:
        raise VaultIntegrityError(f"Vault metadata at {home_path} has unsupported KDF parameters")

    root_key = _derive_root_key(password, salt, n, r, p)
    if not _check_verifier(root_key, salt, verifier):
        raise WrongMasterPasswordError("The master password does not open this vault")
    vault = Vault(home=home_path, master_key=root_key)
    with _VAULT_LOCK:
        _VAULTS[home_path] = vault
    return vault


def _read_key_file(path: str) -> bytes:
    """Read a raw 32-byte X25519 key from *path* (base64 or hex text)."""

    import base64 as _b64

    raw = open(path, "r", encoding="utf-8").read().strip()
    candidates: list[bytes] = []
    try:
        candidates.append(_b64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except Exception:
        pass
    try:
        candidates.append(bytes.fromhex(raw))
    except ValueError:
        pass
    for cand in candidates:
        if len(cand) == 32:
            return cand
    raise VaultIntegrityError(f"not a 32-byte key in base64 or hex ({path})")


def get_vault(home: Path | str | None = None, *, allow_env_unlock: bool = True) -> Vault:
    """Return the unlocked vault for *home* (default: the active Hermes home).

    Fork: when the vault is locked but ``HERMES_MASTER_PASSWORD`` is set
    (daemons, gateways, cron), auto-unlock instead of raising — background
    writers like the log-frame handler must not crash-log on every record
    merely because no TTY prompt is possible. Callers that want strict
    failure pass ``allow_env_unlock=False``."""

    if home is None:
        from hermes_constants import get_hermes_home

        home = get_hermes_home()
    home_path = Path(home).expanduser().resolve()
    with _VAULT_LOCK:
        vault = _VAULTS.get(home_path)
    if vault is None:
        if not vault_exists(home_path):
            raise VaultNotInitializedError(
                f"No vault at {home_path}; run 'hermes secure-vault migrate' first"
            )
        if allow_env_unlock:
            from os import environ as _environ

            key_path = _environ.get("HERMES_VAULT_PRIVATE_KEY")
            if key_path:
                private_key = _read_key_file(key_path)
                return unlock_with_private_key(home_path, private_key)
            env_pw = _environ.get("HERMES_MASTER_PASSWORD")
            if env_pw:
                return unlock(home_path, env_pw)
        raise VaultLockedError(
            f"The vault at {home_path} is locked; supply the master password to unlock"
        )
    return vault


def is_unlocked(home: Path | str | None = None) -> bool:
    if home is None:
        from hermes_constants import get_hermes_home

        home = get_hermes_home()
    home_path = Path(home).expanduser().resolve()
    with _VAULT_LOCK:
        return home_path in _VAULTS


def lock_now(home: Path | str | None = None) -> None:
    """Drop the process-local master key for *home*."""

    if home is None:
        from hermes_constants import get_hermes_home

        home = get_hermes_home()
    home_path = Path(home).expanduser().resolve()
    with _VAULT_LOCK:
        _VAULTS.pop(home_path, None)


def clear_vault_cache() -> None:
    """Forget every process-local unlocked key (tests, profile switches)."""

    with _VAULT_LOCK:
        _VAULTS.clear()


# ---------------------------------------------------------------------------
# Status: inspect the vault WITHOUT unlocking (no crypto import needed).
# ---------------------------------------------------------------------------

# File magic for encrypted envelopes (from hermes_security.io).
# Duplicated so status stays import-free of the crypto stack.
_ENVELOPE_MAGIC = b"HRMVAULT\x00"

# SQLite file header. SQLCipher files are indistinguishable from stdlib SQLite
# by header alone (both start with "SQLite format 3\0"); inside a vaulted home,
# all .db/.sqlite/.sqlite3 files ARE SQLCipher.
_SQLITE_MAGIC = b"SQLite format 3\x00"
_DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")


def _is_in_skip_dir(rel: Path | str) -> bool:
    parts = Path(rel).parts
    return any(part in _SKIP_DIRS for part in parts)


_FRAME_SUFFIXES = (".log", ".jsonl")


def count_encrypted_files(home: Path | str) -> tuple[int, int, int]:
    """Count encrypted files, SQLCipher databases, and frame streams under *home*.

    Returns (envelope_count, db_count, frame_stream_count).  A file is counted
    as an envelope if it starts with the ``HRMVAULT`` magic (config.yaml, .env,
    auth.json, etc.).  Databases are identified by the SQLite header plus a
    ``.db``/``.sqlite``/``.sqlite3`` suffix (inside a vaulted home, these are
    always SQLCipher).  Frame streams are ``*.log`` and ``*.jsonl`` files in a
    vaulted home (encrypted at the per-frame level, not file-level).

    No vault key is needed — this is a metadata-only scan.  Code/artifact trees
    (``hermes-agent``, ``.venv``, ``node_modules``, etc.) are skipped; they
    contain no user content.
    """
    home_path = Path(home).expanduser().resolve()
    envelopes = 0
    dbs = 0
    frames = 0
    if not home_path.is_dir():
        return 0, 0, 0
    for path in sorted(home_path.rglob("*")):
        if not path.is_file():
            continue
        if path.name == _META_FILENAME or path.name.startswith(".hermes-vault."):
            continue
        if path.name.endswith(".lock"):
            continue  # advisory flock sidecars
        rel = path.relative_to(home_path)
        if _is_in_skip_dir(rel):
            continue
        try:
            with open(path, "rb") as fh:
                head = fh.read(16)
        except OSError:
            continue
        if head.startswith(_ENVELOPE_MAGIC):
            # Envelopes: config, .env, auth.json, gateway_state.json, etc.
            envelopes += 1
        elif path.suffix in _DB_SUFFIXES:
            # In a vaulted home every .db/.sqlite file is SQLCipher — its
            # first page is CIPHERTEXT, so there is no plaintext SQLite
            # header to match (only a plaintext-mode db would show one, and
            # those are precisely what this fork refuses to create).
            dbs += 1
        elif path.suffix in _FRAME_SUFFIXES and not head.startswith(_ENVELOPE_MAGIC):
            # Frame streams: encrypted per-frame, no file-level magic.
            # In a vaulted home, .log/.jsonl files ARE frame streams.
            frames += 1
    return envelopes, dbs, frames


def vault_status(home: Path | str | None = None) -> dict:
    """Inspect the vault at *home* WITHOUT unlocking it.

    Returns a dict with:
      - ``home``: resolved home path
      - ``exists``: whether vault metadata is present
      - ``unlocked``: whether this process has the vault unlocked
      - ``meta``: vault metadata dict (KDF params, salt, version) OR None
      - ``encrypted_files``: (envelopes, dbs, frames) tuple
      - ``integrity``: ``ok`` or ``corrupt`` (metadata readable)
    """
    if home is None:
        from hermes_constants import get_hermes_home

        home = get_hermes_home()
    home_path = Path(home).expanduser().resolve()
    exists = vault_exists(home_path)
    unlocked = is_unlocked(home_path)
    meta: dict | None = None
    integrity = "no_vault"
    if exists:
        try:
            meta = _load_meta(home_path)
            if meta is None:
                integrity = "corrupt"
            else:
                integrity = "ok"
        except VaultIntegrityError:
            integrity = "corrupt"
    envelopes, dbs, frames = (0, 0, 0)
    if exists and integrity == "ok":
        envelopes, dbs, frames = count_encrypted_files(home_path)
    return {
        "home": str(home_path),
        "exists": exists,
        "unlocked": unlocked,
        "meta": meta,
        "encrypted_files": (envelopes, dbs, frames),
        "integrity": integrity,
    }
