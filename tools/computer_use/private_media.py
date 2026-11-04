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
from contextvars import ContextVar
from pathlib import Path
from typing import NamedTuple, Optional

from hermes_security.errors import VaultError, VaultLockedError

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
_CAPTURE_OWNER: ContextVar[tuple | None] = ContextVar("computer_use_capture_owner", default=None)


@contextlib.contextmanager
def capture_scope():
    """Capture retention authority before backend startup, approvals and delayed capture work.

    An unavailable owner still permits in-memory desktop control; retained copies refuse
    using this captured result rather than borrowing a later unlock or sibling session.
    """
    from hermes_constants import get_hermes_home
    from hermes_security.session_custody import SessionCustody

    home = get_hermes_home().resolve()
    try:
        owner = (home, SessionCustody.capture(home), None)
    except (VaultError, OSError, ValueError) as exc:
        owner = (home, None, f"{type(exc).__name__}: {exc}")
    token = _CAPTURE_OWNER.set(owner)
    try:
        yield
    finally:
        _CAPTURE_OWNER.reset(token)


def capture_home() -> Path:
    """Return the original retention home without changing backend/approval cache keys."""
    from hermes_constants import get_hermes_home

    owner = _CAPTURE_OWNER.get()
    return owner[0] if owner is not None else get_hermes_home().resolve()


def _retention_custody(home: Path):
    from hermes_security.session_custody import SessionCustody

    owner = _CAPTURE_OWNER.get()
    if owner is None:
        return SessionCustody.capture(home)
    if home.resolve() != owner[0]:
        raise PermissionError("Capture retention belongs to a different profile")
    if owner[1] is None:
        raise VaultLockedError(owner[2])
    owner[1].check()
    return owner[1]


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
        custody = _retention_custody(Path(home))
        path = artifacts.write_bytes(Path(home) / relative, data, home=home, custody=custody)
    except (VaultError, OSError, ValueError) as exc:
        logger.warning("computer_use: %s copy not retained: %s", prefix.rstrip("_"), exc)
        return Retained(unavailable=f"{type(exc).__name__}: {exc}")
    _prune(path.parents[len(directory)], relative, family[2], custody)
    return Retained(path=str(path))


def _prune(selected: Path, relative: Path, family_name: re.Pattern, custody) -> None:
    """Keep the new copy plus the newest older ones; unlink only owned regular files by exact name."""
    from hermes_security.artifacts import _parent

    try:
        custody.check()
        with _parent(selected, relative) as parent:
            older = [name for _, name in _owned_copies(parent, family_name) if name != relative.name]
            for name in older[:max(0, len(older) - (RETAINED_PER_FAMILY - 1))]:
                custody.check()
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(name, dir_fd=parent)
    except (VaultError, OSError) as exc:
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
