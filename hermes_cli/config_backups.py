"""Point-in-time copies of ``config.yaml``: one directory, one naming scheme, bounded count.

Every writer that wants a "before" copy of the user's config (setup wizard, corrupt-file
snapshot, model migrations) goes through :func:`backup_config`. Copies live in
``<HERMES_HOME>/backups/config/`` — ``backups/`` is already excluded from full backups, so they
never nest — as ``config.yaml.<reason>.<YYYYMMDD-HHMMSS>``. A copy identical to the newest one
for the same reason is skipped, and only the newest ``keep`` per reason survive, so repeated
``hermes setup`` runs or a gateway restarting against broken YAML cannot litter the home dir.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BACKUPS_SUBDIR = Path("backups") / "config"
DEFAULT_KEEP = 5

# Names earlier code wrote next to config.yaml (setup wizard, corrupt snapshot, xai migration).
# Moved into the backups dir on first use so they stop accumulating in the home root; hand-named
# copies (``config.yaml.bak-my-note``) are the user's and are never touched.
_LEGACY_SIBLING_GLOBS = ("config.yaml.bak.[0-9]*", "config.yaml.corrupt.*.bak", "config.yaml.bak-pre-migrate-*")


def _sealed_home(path: Path) -> bool:
    """True when *path* lives in a vaulted home (fork). Metadata-only probe, no crypto import."""
    from hermes_security.io import _home_for

    return _home_for(path) is not None


def _read_backup_bytes(backup: Path) -> bytes:
    """Plaintext of a sealed backup (fork). Backups are sealed at their own path with purpose
    ``config``; migration sealed pre-existing ones as ``state``; copies made by the old byte-copy
    still carry ``config.yaml``'s path in their AAD. Try each; the last error is the real one."""
    from hermes_security.errors import VaultIntegrityError
    from hermes_security.io import _home_for
    from hermes_security.vault import get_vault

    raw = backup.read_bytes()
    home = _home_for(backup)
    rel = backup.resolve().relative_to(home).as_posix()
    vault = get_vault(home)
    error: VaultIntegrityError | None = None
    for relpath, purpose in ((rel, "config"), (rel, "state"), ("config.yaml", "config")):
        try:
            return vault.decrypt(raw, purpose=purpose, relpath=relpath)
        except VaultIntegrityError as exc:
            error = exc
    raise error


def backups_dir(config_path: Path) -> Path:
    return config_path.parent / BACKUPS_SUBDIR


def list_config_backups(config_path: Path, reason: Optional[str] = None) -> list[Path]:
    """Existing backups, newest first; filtered to one *reason* when given."""
    root = backups_dir(config_path)
    if not root.is_dir():
        return []
    prefix = f"{config_path.name}.{reason}." if reason else f"{config_path.name}."
    return sorted((p for p in root.iterdir() if p.is_file() and p.name.startswith(prefix)),
                  key=lambda p: p.name, reverse=True)


def backup_config(config_path: Path, reason: str, *, keep: int = DEFAULT_KEEP) -> Optional[Path]:
    """Copy *config_path* to the backups dir; return the new path, or None when skipped/failed.

    Skips when the file is missing/empty, or when the newest backup for *reason* already holds
    identical bytes. Never raises: a failed backup must not block the write it precedes.
    """
    try:
        if not config_path.is_file() or config_path.stat().st_size == 0:
            return None
        root = backups_dir(config_path)
        root.mkdir(parents=True, exist_ok=True)
        _sweep_legacy_siblings(config_path, root)
        existing = list_config_backups(config_path, reason)
        # Fork: direct byte compare — filecmp's stat-signature cache returns a
        # stale "identical" verdict when successive writes share size+mtime
        # (coarse-mtime filesystems), silently skipping backups of CHANGED
        # configs. Configs are tiny; reading bytes is deterministic.
        sealed = _sealed_home(config_path)
        if sealed:
            # Fork: an envelope binds its own path into the AAD, so a byte copy is undecryptable at
            # the backup's path — re-seal the plaintext there instead, and compare plaintext (every
            # seal uses a fresh nonce, so ciphertexts of identical configs never match).
            from hermes_security.io import read_bytes, write_bytes
            current = read_bytes(config_path, purpose="config")
            if existing and current == _read_backup_bytes(existing[0]):
                return None
        elif existing and config_path.read_bytes() == existing[0].read_bytes():
            return None
        dest = root / f"{config_path.name}.{reason}.{time.strftime('%Y%m%d-%H%M%S')}"
        if dest.is_symlink() or dest.exists():  # never write through a planted link
            return None
        if sealed:
            write_bytes(dest, current, purpose="config")
        else:
            shutil.copy2(config_path, dest)
        for stale in [dest, *existing][keep:]:
            stale.unlink(missing_ok=True)
        return dest
    except (OSError, RuntimeError) as exc:  # RuntimeError: vault errors (locked / unauthenticated copy)
        logger.warning("Could not back up %s (%s): %s", config_path, reason, exc)
        return None


def load_newest_good_backup(config_path: Path) -> Optional[dict]:
    """Parse the newest ``good`` backup (the file as it was at the last successful load).

    Returns the raw mapping, or None when there is no usable copy. Older ``good`` copies are not
    tried: a backup that fails to parse means the on-disk copy was damaged after the fact, and
    guessing further back would serve a config the user never saw as current.
    """
    newest = list_config_backups(config_path, "good")[:1]
    if not newest:
        return None
    try:
        from utils import fast_safe_load
        if _sealed_home(newest[0]):
            import io as _io
            data = fast_safe_load(_io.StringIO(_read_backup_bytes(newest[0]).decode("utf-8")))
        else:
            with newest[0].open(encoding="utf-8") as f:
                data = fast_safe_load(f)
    except Exception as exc:
        logger.warning("Last-known-good backup %s is unreadable: %s", newest[0], exc)
        return None
    return data if isinstance(data, dict) else None


def _sweep_legacy_siblings(config_path: Path, root: Path) -> None:
    for pattern in _LEGACY_SIBLING_GLOBS:
        for old in config_path.parent.glob(pattern):
            if not old.is_file() or old.is_symlink():
                continue
            try:
                old.replace(root / old.name)
            except OSError as exc:
                logger.debug("Could not move legacy backup %s: %s", old, exc)
