"""Retained computer_use captures: encrypted screenshots and full element trees.

A capture is as sensitive as the screen it came from (an open password manager, a private chat).
Its retained copies are this profile's encrypted media artifacts, written by the shared
authenticated writer (``hermes_security.artifacts``) and reopened by the existing typed readers:
``vision_analyze`` / ``/artifacts view`` for ``artifacts/media/images/computer_use_<uuid>.<ext>`` and
``read_file`` for ``artifacts/media/documents/elements_<uuid>.json``. A missing, locked or foreign
vault never degrades to a plaintext cache: the capture keeps only its in-memory evidence and the
response says the copy was not retained.

Retention keeps the newest ``RETAINED_PER_FAMILY`` encrypted copies of each family. Plaintext
copies written by older builds (``cache/images``, ``image_cache``, ``cache/computer_use``,
``computer_use_cache``) belong to the owner's explicit repair/export; they are neither rewritten
nor pruned here.
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import stat
import uuid
from pathlib import Path
from typing import NamedTuple, Optional

from hermes_security.errors import VaultError

logger = logging.getLogger(__name__)

RETAINED_PER_FAMILY = 20
_SCREENSHOTS = (("artifacts", "media", "images"), "computer_use_",
                re.compile(r"computer_use_[a-f0-9]{32}\.(?:png|jpg)"))
_ELEMENT_TREES = (("artifacts", "media", "documents"), "elements_",
                  re.compile(r"elements_[a-f0-9]{32}\.json"))


class Retained(NamedTuple):
    """Where one encrypted copy landed, or why none was kept (both None: nothing to retain)."""

    path: Optional[str] = None
    unavailable: Optional[str] = None


NOT_NEEDED = Retained()


def retain_screenshot(home: Path, data: bytes, extension: str) -> Retained:
    """Encrypt the original capture bytes under the owning home ``home``."""
    return _retain(home, _SCREENSHOTS, extension, data)


def retain_element_tree(home: Path, payload: bytes) -> Retained:
    """Encrypt the full, untruncated element tree JSON under the owning home ``home``."""
    return _retain(home, _ELEMENT_TREES, ".json", payload)


def _retain(home: Path, family, extension: str, data: bytes) -> Retained:
    from hermes_security import artifacts

    directory, prefix, _ = family
    relative = Path(*directory, f"{prefix}{uuid.uuid4().hex}{extension}")
    try:
        path = artifacts.write_bytes(Path(home) / relative, data, home=home)
    except (VaultError, OSError, ValueError) as exc:
        logger.warning("computer_use: %s copy not retained: %s", prefix.rstrip("_"), exc)
        return Retained(unavailable=f"{type(exc).__name__}: {exc}")
    _prune(path.parents[len(directory)], relative, family[2])
    return Retained(path=str(path))


def _prune(selected: Path, relative: Path, family_name: re.Pattern) -> None:
    """Keep the new copy plus the newest older ones; unlink only owned regular files by exact name."""
    from hermes_security.artifacts import _parent

    try:
        with _parent(selected, relative) as parent:
            older = [name for _, name in _owned_copies(parent, family_name) if name != relative.name]
            for name in older[:max(0, len(older) - (RETAINED_PER_FAMILY - 1))]:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(name, dir_fd=parent)
    except OSError as exc:
        logger.warning("computer_use: retention of %s not enforced for this capture: %s",
                       relative.parent.as_posix(), exc)


def _owned_copies(parent: int, family_name: re.Pattern) -> list[tuple[int, str]]:
    copies = []
    for name in filter(family_name.fullmatch, os.listdir(parent)):
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid():
            copies.append((info.st_mtime_ns, name))
    return sorted(copies)
