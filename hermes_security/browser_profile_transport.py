"""Bounded, temporary tar transport of a quiescent outgoing browser profile.

Tar is only an anonymous-pipe/RAM transfer container. The browser policy and the
shared authenticated tree archive remain the sole durable secret-state format.
Every tar member is admitted before any destination file is created.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import tarfile

from hermes_platform.memory_capacity import capacity_budget
from hermes_security import browser_profile_archive as profiles
from hermes_security import tree_archive

_CHUNK = 1024 * 1024
_METADATA_ALLOWANCE = 4096
_HEADROOM = 16 * 1024 * 1024


def require_capacity(base: Path, needed: int) -> None:
    """Admit simultaneous copies against current native capacity; this is no reservation."""
    budget = capacity_budget(base)
    if budget.available_bytes is None or budget.available_bytes < needed + _HEADROOM:
        raise tree_archive.ArchiveError(
            f"Protected browser transfer needs {needed + _HEADROOM} available bytes; "
            f"RAM admission refused ({budget.reason}). Existing state was retained")


def receive_bound(base: Path) -> int:
    """Keep headroom for tar metadata and the extracted tree, rather than buffering GiB stdout."""
    budget = capacity_budget(base)
    if budget.available_bytes is None or budget.available_bytes <= _HEADROOM:
        raise tree_archive.ArchiveError(f"Protected browser transfer capacity is unknown or exhausted ({budget.reason})")
    policy = profiles._policy()
    wire = policy.max_bytes + policy.max_entries * 2048 + 10240
    return min(wire, (budget.available_bytes - _HEADROOM) // 10)


def _relative(member: tarfile.TarInfo) -> str | None:
    name = member.name
    if name in (".", "./") and member.isdir():
        return None
    name = name.removeprefix("./")
    if member.isdir():
        name = name.rstrip("/")
    if name.startswith("/"):
        raise tree_archive.ArchiveError("Outgoing profile tar contains an absolute path")
    return tree_archive._relative(name, "outgoing browser transport")


def _kind(member: tarfile.TarInfo) -> int:
    if member.isdir():
        return stat.S_IFDIR
    if member.isreg() and member.sparse is None:
        return stat.S_IFREG
    if member.issym():
        return stat.S_IFLNK
    return 0


def _plan(archive: tarfile.TarFile):
    policy = profiles._policy()
    names, excluded, admitted = set(), set(), []
    size = count = 0
    for member in archive:
        count += 1
        if count > policy.max_entries:
            raise tree_archive.ArchiveError("Outgoing profile tar exceeds its entry bound")
        relative = _relative(member)
        if relative is None:
            continue
        if relative in names:
            raise tree_archive.ArchiveError("Outgoing profile tar repeats an entry")
        names.add(relative)
        kind = _kind(member)
        if any(relative == root or relative.startswith(root + "/") for root in excluded):
            continue
        if policy.excluded and policy.excluded(relative, kind):
            excluded.add(relative)
            continue
        if kind not in (stat.S_IFREG, stat.S_IFDIR):
            raise tree_archive.ArchiveError("Outgoing profile tar contains a link or nonregular entry; retained unchanged")
        if member.size < 0:
            raise tree_archive.ArchiveError("Outgoing profile tar has an invalid file size")
        size += member.size
        if size > policy.max_bytes:
            raise tree_archive.ArchiveError("Outgoing profile tar exceeds its plaintext bound")
        admitted.append((relative, member))
    admitted = [(relative, member) for relative, member in admitted
                if not any(relative == root or relative.startswith(root + "/") for root in excluded)]
    kinds = {relative: member.isdir() for relative, member in admitted}
    for relative in kinds:
        parts = relative.split("/")
        if any(kinds.get("/".join(parts[:index])) is False for index in range(1, len(parts))):
            raise tree_archive.ArchiveError("Outgoing profile tar uses a file as a parent directory")
    return admitted, size, count


def unpack(source, target: Path) -> tree_archive.ArchiveSummary:
    """Validate a complete pinned RAM tar before extracting into a fresh owner-only RAM tree."""
    if not target.is_dir() or target.is_symlink() or list(target.iterdir()):
        raise tree_archive.ArchiveError("Outgoing profile restore requires a fresh empty RAM directory")
    with tarfile.open(fileobj=source, mode="r:") as archive:
        admitted, size, count = _plan(archive)
        require_capacity(target, size + count * _METADATA_ALLOWANCE)
        for relative, member in admitted:
            destination = target.joinpath(*relative.split("/"))
            destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            if member.isdir():
                destination.mkdir(mode=0o700, exist_ok=True)
            else:
                _extract_file(archive, member, destination)
    return profiles.tree_digest(target)


def _extract_file(archive, member, destination: Path) -> None:
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(descriptor, "wb") as output, archive.extractfile(member) as content:
        shutil.copyfileobj(content, output, length=_CHUNK)
        if output.tell() != member.size:
            raise tree_archive.ArchiveError("Outgoing profile tar is incomplete")


def pack(source: Path, target) -> tree_archive.ArchiveSummary:
    """Stream only shared-policy entries from a pinned quiescent tree into the caller's RAM file."""
    policy = profiles._policy()
    before = profiles.tree_digest(source)
    require_capacity(source, before.bytes + before.entries * _METADATA_ALLOWANCE + 10240)
    with tree_archive._source_directory(source, policy.label) as root:
        with tarfile.open(fileobj=target, mode="w|", format=tarfile.PAX_FORMAT) as archive:
            for relative, directory in tree_archive._iter_tree(root, policy):
                _add(archive, root, relative, directory)
    if profiles.tree_digest(source) != before:
        raise tree_archive.ArchiveError("Outgoing profile changed during transfer preparation; original state retained")
    return before


def _add(archive, root, relative: str, directory: bool) -> None:
    member = tarfile.TarInfo(relative)
    member.mode = 0o700 if directory else 0o600
    if directory:
        member.type = tarfile.DIRTYPE
        archive.addfile(member)
    else:
        with tree_archive._source_file(root, "browser profile", relative) as (content, info):
            member.size = info.st_size
            archive.addfile(member, content)
