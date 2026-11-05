"""Selected-home migration scope: profiles below a home are retained as opaque, independent islands.

Conversion and verification of the selected home stop at its direct ``profiles/`` subtree. The
encrypted full rollback still carries every child's private bytes exactly, including each child's
own public credential metadata, so restoring it never needs a child key. Nothing here derives,
unlocks, decrypts or rekeys a child, creates a home, or consults the ambient ``HERMES_HOME``.
"""
from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from typing import Iterator

from hermes_constants import _DELETED_PROFILES_DIR, PROFILE_ID_RE, named_profile_has_identity, named_profile_is_deleted
from hermes_security import migration_state, vault
from hermes_security.errors import VaultIntegrityError, VaultNotInitializedError

_PROFILES = "profiles"
_METADATA = vault._META_FILENAME
_METADATA_LOCK = _METADATA + ".lock"  # a live lock inode is never restored
_KIND = "profile"
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


def _inventory(home: Path) -> list[tuple[str, int]] | None:
    """``(name, lstat mode)`` of each direct entry of a real ``home/profiles``.

    ``[]`` when absent; ``None`` when ``profiles`` itself is a link or file, which is retained as
    one opaque entry and never walked. An unreadable inventory raises instead of reading as empty.
    """
    try:
        fd = os.open(home / _PROFILES, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW | _CLOEXEC)
    except FileNotFoundError:
        return []
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            return None
        raise
    try:
        with os.scandir(fd) as listing:
            return sorted((entry.name, entry.stat(follow_symlinks=False).st_mode) for entry in listing)
    finally:
        os.close(fd)


def children(home: Path) -> list[Path]:
    """Every direct real directory under ``home/profiles``, known or not, so fencing can quiesce it.

    Symlinked entries are preserved by the snapshot as links, never followed or fenced as homes;
    the tombstone directory is catalog metadata, not a home.
    """
    home = Path(home)
    return [home / _PROFILES / name for name, mode in _inventory(home) or ()
            if stat.S_ISDIR(mode) and name != _DELETED_PROFILES_DIR]


def _recognized(profile: Path) -> bool:
    """The existing catalog's live named-profile predicate, applied to this exact path."""
    return (profile.name != "default" and PROFILE_ID_RE.match(profile.name) is not None
            and named_profile_has_identity(profile) and not named_profile_is_deleted(profile))


def _needs_own_vault(profile: Path) -> bool:
    try:
        meta = _metadata(profile)
    except VaultIntegrityError:
        return True  # malformed, unreadable or symlinked credential metadata is never certified
    return meta is None or migration_state._FIELD in meta


def scope(home: Path) -> tuple[list[str], list[str]]:
    """``(opaque, pending)`` home-relative paths of the profiles a selected-home migration retains.

    Every direct entry is retained opaquely. Pending lists recognized live named profiles whose own
    vault is absent, unusable or mid-migration. A valid independent vault stays opaque and is NOT
    certified; unknown directories are reported as data and never initialized.
    """
    home = Path(home)
    entries = _inventory(home)
    if entries is None:
        return [_PROFILES], []
    opaque, pending = [], []
    for name, mode in entries:
        relative = f"{_PROFILES}/{name}"
        opaque.append(relative)
        profile = home / _PROFILES / name
        if stat.S_ISDIR(mode) and _recognized(profile) and _needs_own_vault(profile):
            pending.append(relative)
    return opaque, pending


def _opaque(path: Path) -> dict:
    """Raw-byte identity only: child state is never classified, fingerprinted or decrypted."""
    from hermes_security.migration_resume import file_hash

    if stat.S_ISREG(os.lstat(path).st_mode):
        return {"kind": _KIND, "sha256": file_hash(path)}
    return {"kind": _KIND}


def _is_child_credential(parent: Path, name: str) -> bool:
    if name == _METADATA_LOCK or not (name == _METADATA or name.startswith(_METADATA + ".")):
        return False
    mode = os.lstat(parent / name).st_mode
    return stat.S_ISREG(mode) or stat.S_ISLNK(mode)


def _child_credentials(home: Path) -> Iterator[tuple[Path, Path]]:
    """Child credential metadata the public-state walk omits, under the same code exclusions."""
    if not _inventory(home):
        return

    def failed(exc):
        raise exc

    for directory, subdirectories, files in os.walk(home / _PROFILES, onerror=failed):
        parent = Path(directory)
        links = [name for name in subdirectories if (parent / name).is_symlink()]
        subdirectories[:] = sorted(name for name in subdirectories if name not in links
                                   and not vault._is_in_skip_dir((parent / name).relative_to(home)))
        for name in sorted(files + links):
            relative = (parent / name).relative_to(home)
            if not vault._is_in_skip_dir(relative) and _is_child_credential(parent, name):
                yield parent / name, relative


def snapshot_entries(home: Path) -> Iterator[tuple[Path, Path, dict]]:
    """Full-rollback members: selected state with resume attributes, everything below profiles opaque.

    Child ``.hermes-vault`` metadata and its ``.hermes-vault.*`` companions are added as exact
    opaque members (never the live lock inode), so the rollback reopens under the selected
    credential yet each child's data still needs that child's own credential. The selected home's
    own metadata is carried by the rollback header, not its payload.
    """
    from hermes_security import migrate
    from hermes_security.migration_resume import attributes

    home = Path(home)
    members = set()
    for path, relative in migrate._iter_files(home):
        members.add(relative)
        yield path, relative, _opaque(path) if relative.parts[0] == _PROFILES else attributes(path, relative)
    for path, relative in _child_credentials(home):
        if relative not in members:
            members.add(relative)
            yield path, relative, _opaque(path)


def _check_key_slots(slots) -> None:
    from hermes_security.passphrase import _decode

    if slots is None:
        return
    if not isinstance(slots, list) or not all(isinstance(slot, dict) for slot in slots):
        raise VaultIntegrityError("Invalid vault key-slot metadata")
    for slot in slots:
        if slot.get("type") == "x25519":
            for name, size in (("eph", 32), ("nonce", vault._NONCE_BYTES), ("sealed", vault._ROOT_KEY_BYTES + 16)):
                _decode(slot.get(name), size)


def _check_credentials(meta: dict) -> None:
    """Existing public credential-format readers (versions 1/2 and key slots); no key is derived."""
    from hermes_security import passphrase

    version = meta.get("version")
    if version == 1:
        if "passphrase" in meta or "authentication" in meta:
            raise VaultIntegrityError("Vault metadata downgrade refused")
        passphrase._kdf(meta)
    elif version == passphrase._METADATA_VERSION:
        slot = meta.get("passphrase")
        if not isinstance(slot, dict):
            raise VaultIntegrityError("Missing passphrase slot")
        passphrase._kdf(slot)
        passphrase._decode(slot.get("nonce"), vault._NONCE_BYTES)
        passphrase._decode(slot.get("sealed"), vault._ROOT_KEY_BYTES + 16)
        passphrase._decode(meta.get("authentication"), 32)
    else:
        raise VaultIntegrityError("Unsupported vault metadata version")
    passphrase._decode(meta.get("salt"), vault._SALT_BYTES)
    if len(passphrase._decode(meta.get("verifier"))) != vault._NONCE_BYTES + len(vault._VERIFIER_PLAINTEXT) + 16:
        raise VaultIntegrityError("Invalid vault master-key proof")
    _check_key_slots(meta.get("key_slots"))


def _metadata(directory: Path) -> dict | None:
    """Validated public credential metadata owned by exactly *directory*; None only when absent."""
    from hermes_security.vault_metadata import exact_home

    try:
        os.lstat(directory / _METADATA)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise VaultIntegrityError(f"Vault metadata at {directory} cannot be inspected") from exc
    try:
        if exact_home(directory) != directory:
            raise VaultIntegrityError(f"Vault metadata at {directory} belongs to another path")
        meta = vault._load_meta(directory)
    except (OSError, VaultNotInitializedError) as exc:
        raise VaultIntegrityError(f"Vault metadata at {directory} is unreadable or changed") from exc
    if meta is None:
        raise VaultIntegrityError(f"Vault metadata at {directory} disappeared while it was inspected")
    _check_credentials(meta)
    return meta


def storage_home(home: Path) -> Path:
    """The outermost validated vaulted directory among the canonical selected home and its parents.

    New named rollbacks are placed beside this directory so they stay outside every vaulted
    ancestor. Unsafe ancestor metadata refuses instead of skipping to an arbitrary directory; no
    ancestor vault, key or secret is obtained.
    """
    selected = Path(home).expanduser().resolve()
    outermost = selected
    for directory in (selected, *selected.parents):
        if _metadata(directory) is not None:
            outermost = directory
    return outermost


def _already_encrypted(path: Path, kind: str) -> bool:
    with path.open("rb") as source:
        head = source.read(16)
    if head.startswith(b"HRMVAULT\0"):
        return True
    if kind == "db":
        return head != b"SQLite format 3\0"
    if kind == "frames" and len(head) >= 4:
        # Existing frames have no magic. A plausible complete first record is
        # ambiguous without the original key, so it cannot be treated as text.
        length = int.from_bytes(head[:4], "big")
        return 28 <= length <= path.stat().st_size - 4
    return False


def refuse_ancestor_sealed(home: Path) -> None:
    """Do not shadow the credential that still owns a legacy selected profile."""
    from hermes_security import migrate

    home = Path(home)
    if storage_home(home) == home:
        return
    for path, relative in migrate._iter_files(home, exclude_roots={_PROFILES}):
        if path.is_symlink() or _already_encrypted(path, migrate._classify(path, relative)):
            raise VaultIntegrityError(
                "Selected home has encrypted or linked state without its own credential metadata; "
                "it may still require the original ancestor vault. No child vault was initialized. "
                "Keep the original metadata, credential and rollback; recover a separate plaintext "
                "copy through deliberate offline recovery before migrating it independently. "
                "Automatic cross-vault rekeying is not supported.")
