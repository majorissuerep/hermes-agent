"""Explicit offline conversion of raw browser profiles that older builds kept on persistent storage.

Older builds left the real-profile copy (``browser-profile/<browser>/``) and the ``/browser connect``
debug profile (``chrome-debug/``) as plaintext Chromium state under the home. Encrypting those files
in place would break Chromium, so ``secure-vault migrate``/``repair`` convert each directory into the
owned encrypted archive instead, under the exclusive maintenance fence:

1. refuse while a browser still holds the directory (Chromium ``SingletonLock`` names a live PID);
2. prove a RAM-only workspace before writing anything;
3. stream the directory into its archive, restore that archive into RAM through the real restore
   path, and require the restored tree to match the original byte for byte;
4. only then remove the plaintext directory. Removal is an ordinary unlink: no secure-erase claim is
   made for SSD storage.

Failures before retirement keep the plaintext directory unchanged. Incomplete
retirement retains the verified encrypted recovery archive and reports it.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from hermes_security import browser_profile_archive as archive
from hermes_security.errors import VaultError


def _legacy_identity(home: Path, root: Path) -> tuple[str | None, dict | None]:
    if root == home / archive.LEGACY_DEBUG_ROOT:
        return archive.DEBUG_SLOT, {}
    from hermes_cli.browser_connect import legacy_copy_identity

    identity = legacy_copy_identity(root.name, str(root))
    return (archive.real_slot(root.name), identity) if identity is not None else (None, None)


def _verified(home: Path, root: Path, relative, *, custody, slot: str, digest: str, expected) -> bool:
    from hermes_cli.browser_profile_store import _prove
    from hermes_security.protected_workspace import protected_workspace

    with protected_workspace("browser-repair", prove=_prove) as ram:
        restored = ram / "restored"
        restored.mkdir(mode=0o700)
        archive.replay_archive(home, relative, custody=custody, slot=slot, digest=digest, target=restored)
        return archive.tree_digest(restored) == expected == archive.tree_digest(root)


def _archive_and_retire(home: Path, root: Path, custody) -> str | None:
    """None when ``root`` was archived, verified and retired; otherwise why it was left in place."""
    from hermes_cli.browser_profile_store import _prove, _singleton_owner

    owner = _singleton_owner(root)
    if owner:
        return f"{owner}; close that browser and run repair again"
    slot, identity = _legacy_identity(home, root)
    if slot is None:
        return "not a recognized Hermes browser profile; left in place"
    digest = archive.identity_digest(custody, slot, identity)
    relative = archive.archive_relative(slot, digest)
    if archive.archive_ciphertext_size(home, relative) is not None:
        return "an encrypted archive for this profile already exists; both were kept"
    from hermes_security.protected_workspace import protected_workspace_status

    status = protected_workspace_status(prove=_prove)
    if not status.proven:
        return f"no proven RAM workspace to verify the conversion ({status.reason}); nothing was written"
    expected = archive.tree_digest(root)
    archive.write_archive(home, relative, root, custody=custody, slot=slot, identity=identity, digest=digest)
    try:
        verified = _verified(home, root, relative, custody=custody, slot=slot, digest=digest, expected=expected)
    except BaseException:
        (home / Path(relative)).unlink(missing_ok=True)  # only the archive just written
        raise
    if not verified:
        (home / Path(relative)).unlink()  # only the archive just written; the plaintext stays the copy
        return "the profile changed or did not verify after encryption; left in place"
    if _singleton_owner(root):
        return "a browser opened it during repair; the verified archive was kept and the plaintext left in place"
    custody.check()
    try:
        shutil.rmtree(root)
    except OSError as exc:
        return (f"verified encrypted recovery archive at {relative.as_posix()}; plaintext cleanup "
                f"is incomplete ({type(exc).__name__}: {exc}); recover from the archive before retrying cleanup")
    return None


def convert_legacy_browser_profiles(home: Path | str) -> dict[str, list[str]]:
    """Archive, verify and retire every legacy plaintext browser profile of ``home``.

    Returns ``{"archived": [...], "retained": ["<path> (<reason>)", ...]}`` (home-relative paths).
    """
    from hermes_state.runtime_ownership import OwnershipConflict, exclusive_maintenance

    home = Path(home).expanduser().resolve()
    report: dict[str, list[str]] = {"archived": [], "retained": []}
    roots = archive.legacy_profile_roots(home)
    if not roots:
        return report
    try:
        with exclusive_maintenance([home]):
            custody = archive.capture_custody(home)
            for root in roots:
                shown = root.relative_to(home).as_posix()
                try:
                    reason = _archive_and_retire(home, root, custody)
                except (VaultError, OSError, ValueError) as exc:
                    reason = f"{type(exc).__name__}: {exc}"
                report["archived" if reason is None else "retained"].append(
                    shown if reason is None else f"{shown} ({reason})")
    except (OwnershipConflict, VaultError, PermissionError) as exc:
        done = set(report["archived"]) | {item.split(" (", 1)[0] for item in report["retained"]}
        report["retained"] += [f"{root.relative_to(home).as_posix()} ({exc})" for root in roots
                               if root.relative_to(home).as_posix() not in done]
    return report
