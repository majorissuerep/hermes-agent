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


def replay_archive(home: Path, relative, *, custody, digest: str,
                   target: Path | None = None) -> tree_archive.ArchiveSummary:
    """Authenticate this identity's worker archive end to end; with ``target`` restore into it.

    The caller publishes ``target`` only after this returns; a corrupt or foreign archive raises.
    """
    home, relative = _admitted(home, relative, custody=custody, digest=digest)
    return tree_archive.replay_archive(home, relative, custody=custody, purpose=_purpose(relative),
                                       policy=_policy(), slot=SLOT, digest=digest, target=target)


def archive_ciphertext_size(home: Path, relative, *, digest: str) -> int | None:
    return tree_archive.ciphertext_size(Path(home).expanduser().resolve(), _named(relative, digest))
