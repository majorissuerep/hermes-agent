"""Legacy private-pet groups excluded by the public-code migration walk.

Keep every original byte in the authenticated rollback before invoking the
existing pet repair producer. Unknown/partial/drifted groups remain unresolved;
this module never implements a second pet/media codec.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from hermes_security.errors import VaultIntegrityError


def _walk(home: Path):
    root = home / "pets"
    if not os.path.lexists(root):
        return
    pending = [root]
    while pending:
        path = pending.pop()
        info = os.lstat(path)
        yield path, path.relative_to(home), info
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as listing:
                pending.extend(path / entry.name for entry in sorted(listing, key=lambda entry: entry.name, reverse=True))


def entries(home: Path):
    """Selected pet groups have their own conversion; child-island bytes stay opaque."""
    from hermes_security.migration_profiles import children
    from hermes_security.migration_resume import file_hash

    for owner, kind in [(home, "pet"), *((child, "profile") for child in children(home))]:
        for path, _relative, info in _walk(owner):
            attributes = {"kind": kind}
            if stat.S_ISREG(info.st_mode):
                attributes["sha256"] = file_hash(path)
            yield path, path.relative_to(home), attributes


def remember(member, archive):
    """Small original metadata only; image bytes stay streamed in the encrypted rollback."""
    attributes = {key.removeprefix("HERMES."): value for key, value in member.pax_headers.items()
                  if key.startswith("HERMES.")}
    kind = stat.S_IFDIR if member.isdir() else stat.S_IFLNK if member.issym() else stat.S_IFREG
    original = {**attributes, "size": member.size, "legacy_type": str(kind), "legacy_link": member.linkname}
    if member.isfile() and Path(member.name).name == "pet.json" and member.size <= 64 * 1024:
        try:
            with archive.extractfile(member) as stream:
                original["metadata"] = json.loads(stream.read(64 * 1024 + 1))
        except (ValueError, UnicodeError):
            original["metadata"] = None  # existing producer reports unreadable provenance
    return original


def plan(home, report):
    from hermes_security.migrate import _migrate_generated_pets

    _migrate_generated_pets(home, report, dry_run=True)
    known = set(report.envelopes)
    for _path, relative, info in _walk(home):
        if not stat.S_ISDIR(info.st_mode) and relative.as_posix() not in known:
            report.failures.append(f"{relative}: pet input is not recognized; retained for explicit recovery")


def _completed(home, originals):
    """Admit a fully retired group only when existing authenticated media proves its identity."""
    from agent.pet import private_media

    completed = set()
    for name, original in originals.items():
        relative, metadata = Path(name), original.get("metadata")
        if len(relative.parts) != 3 or relative.name != "pet.json" or not isinstance(metadata, dict):
            continue
        group = relative.parent
        members = {path: value for path, value in originals.items() if Path(path).is_relative_to(group)}
        files = {path: value for path, value in members.items() if value.get("sha256")}
        if not files or any(os.path.lexists(home / path) for path in files):
            continue
        record = private_media.find(group.name)
        if record is not None and _same_pet(home, record, metadata, group, originals):
            completed.update(members)
            thumb = f"pets/.thumbs/{group.name}.png"
            if thumb in originals and not os.path.lexists(home / thumb):
                completed.add(thumb)
    return completed


def _same_pet(home, record, metadata, group, originals):
    from agent.pet import private_media
    from hermes_security import artifacts

    name = str(metadata.get("spritesheetPath") or "spritesheet.webp")
    source = originals.get((group / name).as_posix())
    if source is None or record[2].get("legacyMetadata") != metadata:
        return False
    pet = private_media.installed(record)
    if hashlib.sha256(private_media.read_image(pet.spritesheet, custody=record[3])).hexdigest() != source.get("sha256"):
        return False
    thumb = originals.get(f"pets/.thumbs/{group.name}.png")
    return thumb is None or hashlib.sha256(artifacts.read_bytes(
        pet.directory / "thumbnail.png", home=home, custody=record[3])).hexdigest() == thumb.get("sha256")


def _admit(home, originals):
    from hermes_security.migration_resume import file_hash

    current = {relative.as_posix(): (path, info) for path, relative, info in _walk(home)}
    if current.keys() - originals.keys():
        raise VaultIntegrityError("Legacy pet inputs changed after the verified rollback; original state retained")
    completed = _completed(home, originals)
    for name, original in originals.items():
        if name in completed:
            continue
        if name not in current:
            raise VaultIntegrityError("Legacy pet group is partially retired or missing; recover with its original encrypted rollback")
        path, info = current[name]
        if str(stat.S_IFMT(info.st_mode)) != original.get("legacy_type"):
            raise VaultIntegrityError("Legacy pet input changed type after rollback; retained")
        if stat.S_ISREG(info.st_mode) and file_hash(path) != original.get("sha256"):
            raise VaultIntegrityError("Legacy pet input changed after rollback; retained")
        if stat.S_ISLNK(info.st_mode) and os.readlink(path) != original.get("legacy_link"):
            raise VaultIntegrityError("Legacy pet input link changed after rollback; retained")


def convert(home, originals, report):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_security.migrate import _migrate_generated_pets

    token = set_hermes_home_override(home)
    try:
        _admit(home, originals)
        _migrate_generated_pets(home, report, dry_run=False)
        remaining = [relative.as_posix() for _path, relative, info in _walk(home)
                     if not stat.S_ISDIR(info.st_mode)]
        if remaining and not report.failures:
            report.failures.append("Legacy pet inputs remain unconverted; encrypted rollback retained: " + ", ".join(remaining[:5]))
    finally:
        reset_hermes_home_override(token)
