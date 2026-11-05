"""Authenticated encrypted archives of the durable browser-harness worker state.

The browser harness keeps its workspace/config as real files in RAM while it runs; the durable copy
is one sealed stream per identity under ``HERMES_HOME/browser-worker/``. This is a separate
secret-state domain from owned browser profiles: its own root, opaque ``<64 hex>.hbwa`` names, key
purpose ``browser-worker-archive:v1:<relative>``, identity KDF and fixed ``browser-harness`` slot.
A profile archive path, or a worker archive at another identity's name, is refused before any key
is derived, even under the same vault.

Every regular file and directory is retained, including cache-shaped Chromium directories,
``SingletonLock`` and temporary-named files: runtime sockets and locks live outside the archived
tree. The record format, bounds checks and verification are ``hermes_security.tree_archive``.
Worker headers require ``file_attributes=owner-exec-v1``: each file's authenticated owner-execute
bit also contributes to the completion digest. Restore grants only owner read/write and that
optional owner execute bit; group/world permissions and special mode bits are never restored.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from pathlib import Path, PurePosixPath

from hermes_security import tree_archive

ARCHIVE_ROOT = "browser-worker"
ARCHIVE_SUFFIX = ".hbwa"
SLOT = "browser-harness"
MAX_STATE_BYTES = 2 * 1024 ** 3
MAX_ENTRIES = 250_000
_LABEL = "browser worker state"
_DIGEST = re.compile(r"[0-9a-f]{64}")


def _policy() -> tree_archive.TreePolicy:
    return tree_archive.TreePolicy(label=_LABEL, max_bytes=MAX_STATE_BYTES, max_entries=MAX_ENTRIES,
                                   preserve_owner_exec=True)


def _purpose(relative: PurePosixPath) -> str:
    return "browser-worker-archive:v1:" + relative.as_posix()


def identity_digest(custody, identity: dict) -> str:
    """Keyed digest of the worker slot and its identity; the archive name leaks no identity text."""
    key = custody.derive_key("browser-worker:v1:identity")
    document = tree_archive.canonical_json({"slot": SLOT, "identity": identity})
    return hmac.new(key, document, hashlib.sha256).hexdigest()


def archive_relative(digest: str) -> PurePosixPath:
    """The one durable location of the worker state whose identity digest is ``digest``."""
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        raise PermissionError("A browser worker archive is named by a 64-character lowercase hex digest")
    return PurePosixPath(ARCHIVE_ROOT, digest + ARCHIVE_SUFFIX)


def worker_state_kind(relative) -> str | None:
    parts = PurePosixPath(Path(relative).as_posix()).parts
    if not parts or parts[0] != ARCHIVE_ROOT:
        return None
    if len(parts) == 2:
        from hermes_security.browser_worker_pending import metadata_kind

        metadata = metadata_kind(relative)
        if metadata is not None:
            return metadata
        if parts[1].endswith(ARCHIVE_SUFFIX) and _DIGEST.fullmatch(parts[1][:-len(ARCHIVE_SUFFIX)]):
            return "archive"
        if parts[1].startswith(".stage-"):
            return "stage"
    return "legacy"


def archive_paths(home: Path) -> list[Path]:
    root = Path(home) / ARCHIVE_ROOT
    try:
        entries = sorted(os.scandir(root), key=lambda entry: entry.name)
    except (FileNotFoundError, NotADirectoryError):
        return []
    return [root / entry.name for entry in entries
            if worker_state_kind(Path(ARCHIVE_ROOT, entry.name)) == "archive"
            and entry.is_file(follow_symlinks=False)]


def capture_custody(home: Path):
    """The current request's original unlock lineage for exactly this initialized home."""
    from hermes_security.artifacts import _bound_custody

    return _bound_custody(Path(home).expanduser().resolve())


def _named(relative, digest: str) -> PurePosixPath:
    """Refuse anything but this identity's exact worker archive name."""
    relative = PurePosixPath(Path(relative).as_posix())
    if relative != archive_relative(digest):
        raise PermissionError("Not this identity's owned browser worker archive path")
    return relative


def _digest(relative) -> str:
    if worker_state_kind(relative) != "archive":
        raise PermissionError("Not an owned browser worker archive path")
    return Path(relative).stem


def _admitted(home: Path, relative, *, custody, digest: str) -> tuple[Path, PurePosixPath]:
    """The exact admitted name, with ``custody`` proven to belong to exactly ``home``."""
    from hermes_security.artifacts import _bound_custody

    relative = _named(relative, digest)
    selected = Path(home).expanduser().resolve()
    _bound_custody(selected, custody)
    return selected, relative


def tree_digest(root: Path) -> tree_archive.ArchiveSummary:
    """Digest of exactly the entries a worker archive of ``root`` would hold (every entry)."""
    return tree_archive.tree_digest(root, _policy())


def write_archive(home: Path, relative, source: Path, *, custody, identity: dict,
                  digest: str) -> tree_archive.ArchiveSummary:
    """Seal ``source`` at its admitted worker location; see ``tree_archive.write_archive``.

    Pre-publication failures leave the previous archive and source untouched.
    A post-publication fsync failure explicitly reports uncertain durability.
    """
    home, relative = _admitted(home, relative, custody=custody, digest=digest)
    return tree_archive.write_archive(home, relative, source, custody=custody, purpose=_purpose(relative),
                                      policy=_policy(), slot=SLOT, identity=identity, digest=digest)


def replay_archive(home: Path, relative, *, custody, digest: str | None = None,
                   target: Path | None = None) -> tree_archive.ArchiveSummary:
    """Authenticate this identity's worker archive end to end; with ``target`` restore into it.

    The caller publishes ``target`` only after this returns; a corrupt or foreign archive raises.
    """
    digest = _digest(relative) if digest is None else digest
    home, relative = _admitted(home, relative, custody=custody, digest=digest)
    return tree_archive.replay_archive(home, relative, custody=custody, purpose=_purpose(relative),
                                       policy=_policy(), slot=SLOT, digest=digest, target=target)


def replay_opened(handle, relative, *, custody, digest=None, target=None) -> tree_archive.ArchiveSummary:
    """Restore a caller-pinned WORKER FD with the original path, slot and completion checks."""
    digest = _digest(relative) if digest is None else digest
    _home, relative = _admitted(custody.home, relative, custody=custody, digest=digest)
    return tree_archive.replay_opened(handle, custody=custody, purpose=_purpose(relative),
                                    policy=_policy(), slot=SLOT, digest=digest, target=target)


def authenticate_ciphertext(handle, relative, *, custody) -> tree_archive.ArchiveSummary:
    """Authenticate an opened opaque backup/import with this exact worker name and header."""
    if worker_state_kind(relative) == 'recovery':
        from hermes_security.browser_worker_pending import authenticate_handle

        return authenticate_handle(handle, relative, custody=custody)
    digest = _digest(relative)
    _home, relative = _admitted(custody.home, relative, custody=custody, digest=digest)
    return tree_archive.authenticate_ciphertext(handle, custody=custody, purpose=_purpose(relative),
                                                 policy=_policy(), slot=SLOT, digest=digest)


def archive_ciphertext_size(home: Path, relative, *, digest: str) -> int | None:
    return tree_archive.ciphertext_size(Path(home).expanduser().resolve(), _named(relative, digest))
