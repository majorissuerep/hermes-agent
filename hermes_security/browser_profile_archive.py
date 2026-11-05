"""Authenticated encrypted archives of Hermes-owned browser profiles.

A Chromium profile is live secret state (SQLite cookie/login databases, LevelDB local storage,
preferences). Chromium needs real files, so the active copy lives in a proven RAM-only workspace
(``hermes_cli.browser_profile_store``); this module persists that copy as one durable sealed stream
and restores it. It is a dedicated secret-state domain: archives are never admitted artifact paths,
and the only readers are the owned-profile restore, explicit repair verification, and vault scan.

The record format and its verification live in ``hermes_security.tree_archive``. This module owns
the browser-profile policy: admitted locations, the ``browser-profile-archive:v1:<relative>`` key
purpose, owner slot/identity binding, Chromium exclusions and the persistence bounds.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import os
import stat
from pathlib import Path, PurePosixPath

from hermes_security import tree_archive

ARCHIVE_ROOT = "browser-profile"
LEGACY_DEBUG_ROOT = "chrome-debug"
ARCHIVE_SUFFIX = ".hbpa"
DEBUG_SLOT = "chrome-debug"
REAL_SLOT_PREFIX = "real-profile:"
OUTGOING_SLOT_PREFIX = "outgoing:"
# Runtime ownership metadata kept beside the active RAM copy; never part of the persisted profile.
RUNTIME_BINDING_NAME = ".hermes-browser-binding.json"

# Persistence is not the auth-only initial real-profile snapshot: CacheStorage, service workers,
# unknown app data and temporary-named files can be the only offline copy. Drop only these known
# Chromium layout locations. Names at another depth, and unexpected entry types, are not caches.
_ROOT_RUNTIME_NAMES = frozenset({
    "SingletonLock", "SingletonSocket", "SingletonCookie", "DevToolsActivePort",
    "RunningChromeVersion", "launch-stderr.log", RUNTIME_BINDING_NAME,
})
_ROOT_REGENERABLE_DIRECTORIES = frozenset({
    "ShaderCache", "GrShaderCache", "Crashpad", "Crash Reports", "BrowserMetrics",
})
_PROFILE_CACHE_DIRECTORIES = frozenset({"Cache", "Code Cache", "GPUCache"})

# Bounds: the real-profile copy keeps auth/login state only (tens of MB); a dedicated debug profile
# also keeps site storage. Exceeding a bound fails the persist explicitly and deletes nothing.
MAX_PROFILE_BYTES = 2 * 1024 ** 3
MAX_ENTRIES = 250_000
_LABEL = "browser profile"
_BOUND_HINT = " Clear large site data in that browser profile and retry."


def real_slot(browser: str) -> str:
    return REAL_SLOT_PREFIX + browser


def archive_purpose(relative) -> str:
    return "browser-profile-archive:v1:" + PurePosixPath(relative).as_posix()


def identity_digest(custody, slot: str, identity: dict) -> str:
    """Keyed digest of the exact owner slot and source identity; names leak no account text."""
    key = custody.derive_key("browser-profile:v1:identity")
    document = tree_archive.canonical_json({"slot": slot, "identity": identity})
    return hmac.new(key, document, hashlib.sha256).hexdigest()


def archive_relative(slot: str, digest: str) -> PurePosixPath:
    """Durable location: one debug-profile archive; one archive per real-profile source identity."""
    if slot == DEBUG_SLOT:
        return PurePosixPath(ARCHIVE_ROOT, DEBUG_SLOT + ARCHIVE_SUFFIX)
    if slot.startswith(OUTGOING_SLOT_PREFIX):
        return PurePosixPath(ARCHIVE_ROOT, f"outgoing-{digest[:32]}{ARCHIVE_SUFFIX}")
    return PurePosixPath(ARCHIVE_ROOT, f"real-{digest[:32]}{ARCHIVE_SUFFIX}")


def capture_custody(home: Path):
    """The current request's original unlock lineage for this exact home (never a later unlock)."""
    from hermes_security.profile_binding import capture

    return capture(Path(home))


def browser_state_kind(relative) -> str | None:
    """Classify a home-relative path inside the owned browser-profile roots.

    ``archive`` is a sealed profile, ``stage`` an interrupted publication (ciphertext), and
    ``legacy`` anything an older build left raw (plaintext Chromium state).
    """
    parts = PurePosixPath(Path(relative).as_posix()).parts
    if not parts or parts[0] not in (ARCHIVE_ROOT, LEGACY_DEBUG_ROOT):
        return None
    if parts[0] == ARCHIVE_ROOT and len(parts) == 2:
        if parts[1].endswith(ARCHIVE_SUFFIX):
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
    return [root / e.name for e in entries if e.name.endswith(ARCHIVE_SUFFIX) and e.is_file(follow_symlinks=False)]


def legacy_profile_roots(home: Path) -> list[Path]:
    """Raw profile directories older builds kept on persistent storage, without opening them."""
    home = Path(home)
    found = []
    with contextlib.suppress(FileNotFoundError, NotADirectoryError):
        found += [home / ARCHIVE_ROOT / e.name for e in sorted(os.scandir(home / ARCHIVE_ROOT), key=lambda e: e.name)
                  if e.is_dir(follow_symlinks=False)]
    debug = home / LEGACY_DEBUG_ROOT
    if debug.is_dir() and not debug.is_symlink():
        found.append(debug)
    return found


# --- Profile policy ---------------------------------------------------------------------------

def _excluded(relative: str, kind: int) -> bool:
    """Only known root runtime objects and structural Chromium cache directories are expendable."""
    parts = PurePosixPath(relative).parts
    if len(parts) == 1:
        name = parts[0]
        return (name in _ROOT_RUNTIME_NAMES
                or (kind == stat.S_IFDIR and name in _ROOT_REGENERABLE_DIRECTORIES)
                or (kind == stat.S_IFREG and name == "BrowserMetrics-spare.pma"))
    if len(parts) != 2 or kind != stat.S_IFDIR or parts[1] not in _PROFILE_CACHE_DIRECTORIES:
        return False
    profile = parts[0]
    standard = profile in {"Default", "Guest Profile", "System Profile"}
    numbered = profile.startswith("Profile ") and profile[8:].isascii() and profile[8:].isdigit()
    return standard or numbered


def _policy() -> tree_archive.TreePolicy:
    """Read the published bounds per call so a configured or patched bound governs that call."""
    return tree_archive.TreePolicy(label=_LABEL, max_bytes=MAX_PROFILE_BYTES, max_entries=MAX_ENTRIES,
                                   excluded=_excluded, bound_hint=_BOUND_HINT)


def _admitted(relative) -> PurePosixPath:
    relative = PurePosixPath(Path(relative).as_posix())
    if browser_state_kind(relative) != "archive":
        raise PermissionError("Not an owned browser profile archive path")
    return relative


def tree_digest(root: Path) -> tree_archive.ArchiveSummary:
    """Digest of exactly the entries an archive of ``root`` would hold (byte-identity proof)."""
    return tree_archive.tree_digest(root, _policy())


def write_archive(home: Path, relative, source: Path, *, custody, slot: str, identity: dict,
                  digest: str) -> tree_archive.ArchiveSummary:
    """Seal ``source`` at its admitted profile location; see ``tree_archive.write_archive``.

    Pre-publication failures leave the previous archive and source untouched.
    A post-publication fsync failure explicitly reports uncertain durability;
    the new authenticated archive and active source are retained.
    """
    relative = _admitted(relative)
    return tree_archive.write_archive(home, relative, source, custody=custody, purpose=archive_purpose(relative),
                                      policy=_policy(), slot=slot, identity=identity, digest=digest)


def authenticate_ciphertext(handle, relative, *, custody) -> tree_archive.ArchiveSummary:
    """Authenticate an opened backup/staged archive for its logical destination; restore no plaintext."""
    relative = _admitted(relative)
    return tree_archive.authenticate_ciphertext(handle, custody=custody, purpose=archive_purpose(relative),
                                                 policy=_policy())


def replay_opened(handle, relative, *, custody, slot: str, digest: str,
                  target: Path | None = None) -> tree_archive.ArchiveSummary:
    """Use an original opened archive; never resolve a replacement between passes."""
    relative = _admitted(relative)
    return tree_archive.replay_opened(handle, custody=custody, purpose=archive_purpose(relative),
                                      policy=_policy(), slot=slot, digest=digest, target=target)


def replay_archive(home: Path, relative, *, custody, slot: str | None = None, digest: str | None = None,
                   target: Path | None = None) -> tree_archive.ArchiveSummary:
    """Authenticate one owned archive end to end; with ``target`` restore into that fresh directory.

    ``slot``/``digest`` bind the restore to the exact owner slot and source identity. The caller
    publishes ``target`` only after this returns; a corrupt or foreign archive raises.
    """
    relative = _admitted(relative)
    return tree_archive.replay_archive(home, relative, custody=custody, purpose=archive_purpose(relative),
                                       policy=_policy(), slot=slot, digest=digest, target=target)


def archive_ciphertext_size(home: Path, relative) -> int | None:
    return tree_archive.ciphertext_size(home, _admitted(relative))
