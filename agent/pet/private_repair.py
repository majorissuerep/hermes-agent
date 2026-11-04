"""Explicit offline conversion of all historical profile-owned pet instances.

Published rename rewrote id/displayName and moved directories without retaining
catalog provenance (32f837add1). Preserve each current logical identity instead
of guessing its origin. Public immutable source catalog caches are independent.
Raw instance paths are retired only after encrypted bytes and metadata verify.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat

from agent.pet import private_media
from agent.provider_media import generation_scope
from hermes_security import artifacts
from hermes_security.errors import VaultError

_SPRITES = {"spritesheet.webp", "spritesheet.png", "sprite.webp", "sprite.png"}
_MAX_IMAGE = 128 * 1024 * 1024


@contextmanager
def _directory(home, relative):
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(home, flags)
    try:
        for part in relative.parts:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            if os.fstat(descriptor).st_uid != os.getuid():
                raise PermissionError("Historical pet directory has a different owner")
        yield descriptor
    finally:
        os.close(descriptor)


def _read(home, relative, maximum):
    with _directory(home, relative.parent) as parent:
        fd = os.open(relative.name, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise PermissionError("Historical pet needs an owned regular file")
            raw = source.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Historical generated pet exceeds its read bound")
    return raw, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def candidates(home, *, unknown=None):
    root = Path(home) / "pets"
    if not root.exists():
        return []
    if root.is_symlink():
        raise PermissionError("Historical pets directory is a symlink; inspect it before repair")
    found = []
    for child in sorted(root.iterdir()):
        if child.name == ".thumbs" or not private_media._valid_identity(child.name):
            continue
        if child.is_symlink():
            if unknown is not None:
                unknown.append(child.relative_to(home).as_posix())
            continue
        if not child.is_dir():
            continue
        relative = child.relative_to(home) / "pet.json"
        if not (home / relative).exists() and not any((child / name).exists() for name in _SPRITES):
            if unknown is not None:
                unknown.append(child.relative_to(home).as_posix())
            continue
        try:
            raw, _ = _read(home, relative, 64 * 1024)
            metadata = json.loads(raw)
        except (OSError, ValueError):
            if unknown is not None:
                unknown.append(relative.as_posix())
            continue  # Report uncertainty; never infer that unreadable metadata is public.
        if isinstance(metadata, dict):
            found.append((child.name, metadata, relative))
        elif unknown is not None:
            unknown.append(relative.as_posix())
    return found


def inventory(home):
    paths, unknown = [], []
    for slug, metadata, relative in candidates(home, unknown=unknown):
        paths.append(relative.as_posix())
        name = str(metadata.get("spritesheetPath") or "spritesheet.webp")
        if name in _SPRITES:
            paths.append((relative.parent / name).as_posix())
        thumb = Path("pets/.thumbs") / (slug + ".png")
        if (home / thumb).exists():
            paths.append(thumb.as_posix())
    thumbs = Path(home) / "pets/.thumbs"
    if thumbs.is_symlink():
        unknown.append("pets/.thumbs")
    elif thumbs.is_dir():
        unknown.extend(path.relative_to(home).as_posix() for path in thumbs.iterdir()
                       if path.relative_to(home).as_posix() not in paths)
    return paths, unknown


def plaintext_paths(home):
    known, unknown = inventory(home)
    return known + unknown


def _remove_same(home, relative, original):
    current = _read(home, relative, len(original[0]))
    if current != original:
        raise RuntimeError("Historical pet changed during repair; its source was retained")
    with _directory(home, relative.parent) as parent:
        info = os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != original[1]:
            raise RuntimeError("Historical pet identity changed during repair; its source was retained")
        os.unlink(relative.name, dir_fd=parent)


def _convert(home, slug, metadata, relative):
    name = str(metadata.get("spritesheetPath") or "spritesheet.webp")
    if name not in _SPRITES:
        raise ValueError("Historical generated pet has an unknown spritesheet reference; export it before repair")
    with _directory(home, relative.parent) as parent:
        if set(os.listdir(parent)) != {"pet.json", name}:
            raise ValueError("Historical generated pet has extra files; export them before repair")
    source = relative.parent / name
    original = _read(home, source, _MAX_IMAGE)
    original_metadata = _read(home, relative, 64 * 1024)
    if json.loads(original_metadata[0]) != metadata:
        raise RuntimeError("Historical pet metadata changed during repair; its source was retained")
    record = private_media.find(slug)
    if record is None:
        private_media.register(original[0], slug=slug, display_name=metadata.get("displayName") or slug,
                               description=metadata.get("description") or "", legacy_metadata=metadata,
                               created_by=str(metadata.get("createdBy") or ""))
        record = private_media.find(slug)
    pet = private_media.installed(record)
    if (private_media.read_image(pet.spritesheet, custody=record[3]) != original[0]
            or record[2].get("legacyMetadata") != metadata):
        raise ValueError("A different generated pet already owns this identity; both copies were retained")
    thumb = Path("pets/.thumbs") / (slug + ".png")
    if (home / thumb).exists():
        raw_thumb = _read(home, thumb, _MAX_IMAGE)
        destination = pet.directory / "thumbnail.png"
        artifacts.write_bytes(destination, raw_thumb[0], home=home, custody=record[3], overwrite=True)
        if artifacts.read_bytes(destination, home=home, custody=record[3]) != raw_thumb[0]:
            raise ValueError("Historical generated pet thumbnail did not verify")
        record[3].check()
        _remove_same(home, thumb, raw_thumb)
    record[3].check()
    _remove_same(home, source, original)
    record[3].check()
    _remove_same(home, relative, original_metadata)
    with _directory(home, relative.parent.parent) as parent:
        os.rmdir(relative.parent.name, dir_fd=parent)


def repair(home, report):
    try:
        _, unknown = inventory(home)
        selected = candidates(home)
    except OSError as exc:
        report["unrecoverable"].append(f"pets: {type(exc).__name__}: {exc}")
        return
    report["unrecoverable"].extend(path + ": pet contents are not safely convertible; explicitly export before inspection"
                                   for path in unknown)
    if not selected:
        return
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(home)
    try:
        with generation_scope():
            for slug, metadata, relative in selected:
                try:
                    _convert(home, slug, metadata, relative)
                    report["sealed"].append(relative.parent.as_posix() + " -> artifacts/generated/pet-store")
                except (OSError, ValueError, RuntimeError, VaultError) as exc:
                    report["unrecoverable"].append(f"{relative.parent}: {type(exc).__name__}: {exc}")
    finally:
        reset_hermes_home_override(token)
