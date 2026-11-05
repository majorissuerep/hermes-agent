"""Owned browser media: RAM-staged capture, encrypted persistence, RAM-staged re-reads.

Private page pixels are profile runtime output, not a regenerable cache. A browser writes its
capture into a proven RAM-only protected workspace; the capture is sealed with the shared
encrypted artifact writer (``hermes_security.artifacts``) before any analysis, and analysis reads
the RAM copy. Later image consumers decrypt an own-profile browser artifact into memory and
prepare it there; file-based tools that need a real path (SVG rasterizer fallbacks) get a fresh
protected workspace. Nothing here writes plaintext pixels to persistent storage, and nothing falls
back when RAM or the vault is missing.

Legacy plaintext captures under ``cache/screenshots`` and ``browser_screenshots`` are left in
place for the owner's migration; they are neither rewritten nor pruned here.
"""
from __future__ import annotations

import contextlib
import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path

from hermes_platform.memory_protection import RamProof, ram_only_directory_proof
from hermes_security.errors import VaultError

BROWSER_MEDIA_ROOT = ("artifacts", "browser")
SCREENSHOT_SUBDIR = (*BROWSER_MEDIA_ROOT, "screenshots")  # agent-browser captures (24h retention)
CAMOFOX_SUBDIR = (*BROWSER_MEDIA_ROOT, "camofox")  # Camofox captures (never pruned, as before)
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


class MediaCustodyError(RuntimeError):
    """Private media could not be staged in RAM or sealed; no plaintext copy was persisted."""


def _prove(path: Path) -> RamProof:
    """RAM-proof seam for unit contracts; production always uses the live host proof."""
    return ram_only_directory_proof(path)


@contextlib.contextmanager
def protected_media_workspace(purpose: str) -> Iterator[Path]:
    """A proven RAM-only 0700 scratch directory, removed on success and failure alike."""
    from hermes_security.protected_workspace import ProtectedWorkspaceUnavailable, protected_workspace

    with contextlib.ExitStack() as stack:
        try:
            workspace = stack.enter_context(protected_workspace(purpose, prove=_prove))
        except ProtectedWorkspaceUnavailable as exc:
            raise MediaCustodyError(
                f"Private media needs a proven RAM-only protected workspace ({exc}). "
                "Set security.protected_work_dir to an owner-only ramfs directory (or use a RAM-only "
                "XDG_RUNTIME_DIR with no active swap) and retry."
            ) from exc
        yield workspace


def _seal(data: bytes, home: Path, subdir: tuple[str, ...]) -> Path:
    from hermes_security import artifacts

    destination = Path(home).joinpath(*subdir) / f"browser_screenshot_{uuid.uuid4().hex}.png"
    try:
        return artifacts.write_bytes(destination, data, home=home)
    except (VaultError, OSError, ValueError) as exc:
        raise MediaCustodyError(
            f"The screenshot could not be stored encrypted ({exc}); the capture was discarded. "
            "Unlock this profile's vault and retry."
        ) from exc


def seal_capture(staged: Path, home: Path) -> Path:
    """Encrypt a RAM-staged agent-browser capture into this profile's screenshot artifacts."""
    return _seal(staged.read_bytes(), home, SCREENSHOT_SUBDIR)


def seal_camofox_capture(data: bytes, home: Path) -> Path:
    """Encrypt in-memory Camofox screenshot bytes; no plaintext file is ever written."""
    return _seal(data, home, CAMOFOX_SUBDIR)


def owned_media_path(source: str) -> Path | None:
    """The filesystem path a media source names when it lies under a reserved private root.

    Includes other profiles' vaulted roots so callers refuse them instead of reading them raw.
    """
    from hermes_security.artifacts import is_artifact_path

    text = (source or "").strip() if isinstance(source, str) else ""
    if text.lower().startswith("file://"):
        text = text[len("file://"):]
    elif not text or _SCHEME.match(text):
        return None
    path = Path(os.path.abspath(os.path.expanduser(text)))
    return path if is_artifact_path(path) else None


def read_owned_media(path: Path, *, home: Path | None = None, custody=None) -> bytes:
    """Authenticate and decrypt one owned image artifact of ``home`` into memory.

    ``home`` defaults to the bound profile; callers outside a profile scope (attach/resume RPCs)
    pass the session's owner explicitly. Another profile's artifact is refused, never read raw."""
    from hermes_constants import get_hermes_home
    from hermes_security import artifacts
    from hermes_security.artifact_paths import is_image_relative_path

    home = get_hermes_home() if home is None else Path(home)
    _, relative = artifacts._location(path, home=home)
    if not is_image_relative_path(relative):
        raise PermissionError("Only owned image artifacts can be read as images")
    return artifacts.read_bytes(path, home=home, custody=custody)
