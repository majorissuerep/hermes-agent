"""Encrypted profile pet instances; public source catalog assets stay independent.

An authenticated registry publishes opaque image objects. Renaming changes the
logical identity in that registry, so path-bound ciphertext never needs moving.
"""
from __future__ import annotations

from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import re
import secrets
import stat

from hermes_security import artifacts
from hermes_security.errors import VaultError, VaultIntegrityError

ROOT = Path("artifacts/generated/pet-store")
REGISTRY = ROOT / "registry.json"
_MAX_REGISTRY = 1024 * 1024
_MAX_PIXELS = 64_000_000


def _owner():
    from agent.provider_media import _output_custody

    return _output_custody()


def _check_scope(custody):
    from hermes_constants import get_hermes_home

    if custody.vault.home != get_hermes_home().resolve():
        raise PermissionError("Generated pet belongs to a different profile")
    custody.check()


def _load(home, custody):
    path = home / REGISTRY
    if not path.exists():
        if any((home / ROOT).glob("[0-9a-f]" * 32)):
            raise VaultIntegrityError("Generated pet registry is missing; use explicit offline recovery before repair")
        return {}
    raw = artifacts.read_bytes(path, home=home, custody=custody)
    if len(raw) > _MAX_REGISTRY:
        raise VaultIntegrityError("Generated pet registry exceeds its byte limit")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise VaultIntegrityError("Generated pet registry is damaged") from exc
    if not isinstance(payload, dict) or payload.get("format") != 1 or not isinstance(payload.get("pets"), dict):
        raise VaultIntegrityError("Generated pet registry is damaged")
    for slug, entry in payload["pets"].items():
        if (not _valid_identity(slug) or not isinstance(entry, dict)
                or not re.fullmatch(r"[0-9a-f]{32}", str(entry.get("object", "")))
                or entry.get("spritesheetPath") not in {"spritesheet.webp", "spritesheet.png"}):
            raise VaultIntegrityError("Generated pet registry contains an invalid identity")
    return payload["pets"]


def _valid_identity(slug):
    return isinstance(slug, str) and slug not in {"", ".", ".."} and "\x00" not in slug and Path(slug).name == slug


def _registry_bytes(entries):
    raw = json.dumps({"format": 1, "pets": entries}, ensure_ascii=False, sort_keys=True).encode()
    if len(raw) > _MAX_REGISTRY:
        raise ValueError("Generated pet registry exceeds its byte limit")
    return raw


def _publish(home, custody, entries):
    artifacts.write_bytes(home / REGISTRY, _registry_bytes(entries), home=home, custody=custody, overwrite=True)


@contextmanager
def _locked(home, custody, *, write=True):
    from hermes_platform.processes import release_file_lock, try_acquire_file_lock

    custody.check()
    with artifacts._parent(home, ROOT / "registry.lock", create=write) as parent:
        flags = (os.O_RDWR | os.O_CREAT) if write else os.O_RDONLY
        fd = os.open("registry.lock", flags | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent)
        with os.fdopen(fd, "r+b" if write else "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise PermissionError("Generated pet registry lock is not a private owned regular file")
            if not try_acquire_file_lock(handle, shared=not write):
                raise VaultError("Generated pet store is busy; retry the existing command")
            try:
                custody.check()
                yield
            finally:
                release_file_lock(handle)  # Keep the live lock inode; never unlink it.


def records():
    home, custody = _owner()
    if not (home / ROOT).exists():
        return []
    try:
        with _locked(home, custody, write=False):
            return [(slug, home / ROOT / entry["object"], dict(entry), custody)
                    for slug, entry in _load(home, custody).items()]
    except (OSError, ValueError) as exc:
        raise VaultIntegrityError(f"Generated pet store is unavailable: {exc}") from exc


def find(slug):
    return next((record for record in records() if record[0] == slug), None)


def read_image(path, *, custody=None):
    """Read only an authorized artifact or an ordinary caller-provided/catalog image."""
    from agent.file_safety import raise_if_model_read_blocked
    from tools.browser_media_custody import owned_media_path
    from hermes_security.artifact_paths import is_image_relative_path

    path = Path(path)
    raise_if_model_read_blocked(str(path))
    if owned_media_path(str(path)) is not None:
        home, selected = _owner() if custody is None else (custody.vault.home, custody)
        _check_scope(selected)
        relative = artifacts._location(path, home=home)[1]
        if not is_image_relative_path(relative):
            raise PermissionError("Only owned image artifacts may be rendered as pets")
        return artifacts.read_bytes(path, home=home, custody=selected)
    return path.read_bytes()


def rgba(source):
    from PIL import Image

    if isinstance(source, (str, Path)):
        source = read_image(source)
    if isinstance(source, (bytes, bytearray)):
        with Image.open(io.BytesIO(source)) as opened:
            if opened.width * opened.height > _MAX_PIXELS:
                raise ValueError("Generated pet image exceeds its pixel limit")
            return opened.convert("RGBA")
    if source.width * source.height > _MAX_PIXELS:
        raise ValueError("Generated pet image exceeds its pixel limit")
    return source.convert("RGBA")


def _encoded(source):
    if isinstance(source, (bytes, bytearray)):
        raw = bytes(source)
        rgba(raw)  # Validate without changing already accepted bytes.
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            return raw, "spritesheet.png"
        if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
            return raw, "spritesheet.webp"
        raise ValueError("Retained pet sprites require PNG or WebP bytes")
    output = io.BytesIO()
    rgba(source).save(output, format="WEBP", lossless=True, quality=100, method=6, exact=True)
    return output.getvalue(), "spritesheet.webp"


def register(source, *, slug, display_name, description, legacy_metadata=None, created_by="generator"):
    home, custody = _owner()
    raw, filename = _encoded(source)
    with _locked(home, custody):
        entries = _load(home, custody)
        identifier = secrets.token_hex(16)
        entry = {"object": identifier, "displayName": display_name or slug, "description": description or "",
                 "spritesheetPath": filename, "createdBy": created_by}
        if legacy_metadata is not None:
            entry["legacyMetadata"] = legacy_metadata
        entries[slug] = entry
        _registry_bytes(entries)  # Reject oversized metadata before publishing any image object.
        destination = home / ROOT / identifier / filename
        artifacts.write_bytes(destination, raw, home=home, custody=custody)
        if artifacts.read_bytes(destination, home=home, custody=custody) != raw:
            raise ValueError("Generated pet output did not verify")
        _publish(home, custody, entries)
    return destination.parent


def installed(record):
    from agent.pet.store import InstalledPet

    slug, directory, entry, custody = record
    _check_scope(custody)
    sheet = directory / entry["spritesheetPath"]
    if directory.is_symlink() or not sheet.is_file() or sheet.is_symlink():
        raise VaultIntegrityError("Generated pet media is missing or unsafe; recover this profile before retrying")
    return InstalledPet(slug, str(entry.get("displayName") or slug), str(entry.get("description") or ""),
                        directory, sheet, str(entry.get("createdBy") or ""), custody)


def capture_image_custody(path):
    from tools.browser_media_custody import owned_media_path

    if owned_media_path(str(path)) is None:
        return None
    home = artifacts._location(path)[0]
    from agent.provider_media import _output_custody

    return _output_custody(home)[1]


def thumbnail(record):
    from agent.pet.store import _thumbnail_data

    custody = record[3]
    home = custody.vault.home
    pet = installed(record)
    target = pet.directory / "thumbnail.png"
    if target.exists():
        return artifacts.read_bytes(target, home=home, custody=custody)
    raw = _thumbnail_data(read_image(pet.spritesheet, custody=custody))
    artifacts.write_bytes(target, raw, home=home, custody=custody)
    return raw


def export(record):
    import zipfile

    slug, directory, entry, custody = record
    raw = read_image(directory / entry["spritesheetPath"], custody=custody)
    metadata = dict(entry.get("legacyMetadata") or {})
    metadata.update({key: value for key, value in entry.items() if key not in {"object", "legacyMetadata"}})
    metadata["id"] = slug
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(slug + "/pet.json", json.dumps(metadata, ensure_ascii=False))
        archive.writestr(slug + "/" + entry["spritesheetPath"], raw)
    custody.check()
    return slug + ".zip", output.getvalue()


def rename(slug, display_name, desired, *, public_conflict=False):
    home, custody = _owner()
    with _locked(home, custody):
        entries = _load(home, custody)
        if slug not in entries:
            return None
        selected = desired if desired not in entries and not public_conflict else slug
        entry = entries.pop(slug)
        entry["displayName"] = display_name
        entries[selected] = entry
        _publish(home, custody, entries)
    return selected


def remove(slug):
    home, custody = _owner()
    with _locked(home, custody):
        entries = _load(home, custody)
        if slug not in entries:
            return False
        entry = entries.pop(slug)
        # Publish the deliberate deletion first. The encrypted files stay orphaned
        # if revocation interrupts cleanup; they are never served by another alias.
        _publish(home, custody, entries)
        directory = ROOT / entry["object"]
        with artifacts._parent(home, directory / "spritesheet.webp") as parent:
            custody.check()
            for name in ("spritesheet.webp", "spritesheet.png", "thumbnail.png"):
                try:
                    os.unlink(name, dir_fd=parent)
                except FileNotFoundError:
                    continue
    return True
