"""Contained bootstrap extraction for interpreters predating tarfile filters."""
from __future__ import annotations

import os
from pathlib import Path
import tarfile


def _contained(destination: Path, name: str) -> Path:
    target = (destination / name).resolve()
    if os.path.isabs(name) or not target.is_relative_to(destination):
        raise ValueError(f"Archive member escapes its destination: {name!r}")
    return target


def extract(archive: tarfile.TarFile, destination: Path) -> None:
    """Never apply archive ownership, special files, or unfiltered link fallbacks.

    This small bootstrap path intentionally rejects forward hard links and
    duplicate files. Current interpreters use their stdlib data filter instead.
    """
    destination = destination.resolve()
    for member in archive:
        target = _contained(destination, member.name)
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise ValueError(f"Unsupported bootstrap archive member: {member.name!r}")
        if target.exists() and not (member.isdir() and target.is_dir()):
            raise ValueError(f"Duplicate bootstrap archive member: {member.name!r}")
        if member.islnk():
            source = _contained(destination, member.linkname)
            if not source.is_file():
                raise ValueError(f"Bootstrap hard link requires an existing file: {member.name!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(source, target)
            continue
        if member.issym():
            _contained(destination, str(Path(member.name).parent / member.linkname))
            target.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(member.linkname, target)
            continue
        archive.extract(member, destination, set_attrs=False)
        target.chmod(0o755 if member.isdir() or member.mode & 0o100 else 0o644)
