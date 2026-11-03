"""Descriptor-pinned cleanup of exact private directories entrusted to a child supervisor."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat


def capture_directories(paths) -> list[dict]:
    """Retain each parent inode and the exact owned directory's identity."""
    records = []
    try:
        for value in paths:
            path = Path(value)
            if (not path.is_absolute() or path.parent == path or path.name in {"", ".."}
                    or path != path.resolve(strict=True)):
                raise ValueError("Managed cleanup requires a canonical absolute private directory")
            parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
                        or info.st_uid != os.geteuid()):
                    raise ValueError("Managed cleanup directory is not private and owned")
                records.append({"parent": parent, "name": path.name,
                                "device": info.st_dev, "inode": info.st_ino})
            except BaseException:
                os.close(parent)
                raise
        return records
    except BaseException:
        close_directories(records)
        raise


def close_directories(records: list[dict]) -> None:
    for record in records:
        os.close(record["parent"])


def remove_directories(records: list[dict]) -> None:
    """Remove only the captured inode; never follow a replacement or a symlink."""
    try:
        for record in records:
            try:
                info = os.stat(record["name"], dir_fd=record["parent"], follow_symlinks=False)
            except FileNotFoundError:
                continue
            if (not stat.S_ISDIR(info.st_mode)
                    or (info.st_dev, info.st_ino) != (record["device"], record["inode"])):
                raise RuntimeError("Managed cleanup directory identity changed; replacement retained")
            shutil.rmtree(record["name"], dir_fd=record["parent"])
    finally:
        close_directories(records)
