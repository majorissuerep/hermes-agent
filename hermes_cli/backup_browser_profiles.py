"""Ciphertext-only disaster recovery of owned browser state, separate from portable exports."""
from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import stat
import zipfile
from pathlib import Path

from hermes_security import browser_profile_archive as archive
from hermes_security import browser_worker_archive as workers
from hermes_security.errors import VaultError

def browser_relative(relative) -> Path | None:
    """The secret-state path, only at an owning home or named-profile root."""
    parts = Path(relative).parts
    if len(parts) > 2 and parts[0] == "profiles":
        parts = parts[2:]
    if parts and parts[0] in {archive.ARCHIVE_ROOT, archive.LEGACY_DEBUG_ROOT, workers.ARCHIVE_ROOT}:
        return Path(*parts)
    return None


def current_archive(relative) -> bool:
    inner = browser_relative(relative)
    return bool(inner is not None and (archive.browser_state_kind(inner) == "archive"
                                      or workers.worker_state_kind(inner) in {"archive", "recovery"})
                and not inner.name.startswith(".stage-"))


def skip_import(relative) -> bool:
    return browser_relative(relative) is not None and not current_archive(relative)


def current_entries(home: Path, relative_parent: Path):
    """Only direct, regular current archives; never descend into legacy cookie trees."""
    parts = relative_parent.parts
    if parts and not (len(parts) == 2 and parts[0] == "profiles"):
        return
    owner = home / relative_parent
    from hermes_security.browser_worker_pending import paths

    for path in [*archive.archive_paths(owner), *workers.archive_paths(owner), *paths(owner, kind='recovery')]:
        if current_archive(path.relative_to(owner)):
            yield path, path.relative_to(home)


class _DigestReader:
    def __init__(self, handle):
        self.handle, self.digest = handle, hashlib.sha256()

    def fileno(self):
        return self.handle.fileno()

    def read(self, size):
        data = self.handle.read(size)
        self.digest.update(data)
        return data


def write_ciphertext(zf, path: Path, relative) -> str | None:
    """Copy opaque bytes even when locked; report known damage without dropping recoverable data."""
    from hermes_security.artifacts import _parent
    from hermes_security.vault import is_unlocked

    home, inner = path.parent.parent, Path(path.parent.name) / path.name
    codec = workers if inner.parts[0] == workers.ARCHIVE_ROOT else archive
    with _parent(home, inner) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise PermissionError("Browser backup requires an owned regular archive")
            warning = None
            verified_digest = None
            if is_unlocked(home):
                try:
                    authenticated = _DigestReader(handle)
                    codec.authenticate_ciphertext(authenticated, inner, custody=codec.capture_custody(home))
                    verified_digest = authenticated.digest.digest()
                except (VaultError, OSError, ValueError) as exc:
                    warning = f"Browser archive copied unchanged but authentication failed ({type(exc).__name__})"
            else:
                print(f"  {relative}: copied opaque browser state; locked vault, validate during import")
            handle.seek(0)
            checked = hashlib.file_digest(handle, "sha256")
            if verified_digest is not None and checked.digest() != verified_digest:
                warning = "Browser archive copied unchanged but changed since authentication; verify before restore"
            handle.seek(0)
            copied = _DigestReader(handle)
            entry = zipfile.ZipInfo(str(relative))
            entry.external_attr = (stat.S_IFREG | 0o600) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            with zf.open(entry, "w") as output:
                shutil.copyfileobj(copied, output, length=1024 * 1024)
            if copied.digest.digest() != checked.digest():
                raise OSError("Browser archive changed during backup; no complete backup was published")
            return warning


def prepare_import(staged: Path, target: Path, home: Path) -> bool:
    """True for authenticated current ciphertext; refuse legacy or interrupted browser state."""
    relative = target.relative_to(home)
    if browser_relative(relative) is None:
        return False
    if not current_archive(relative):
        raise OSError("Legacy/interrupted browser state must be repaired in its original home before backup")
    if target.parent.parent != home:
        raise OSError("Browser archives require their own initialized profile, never an ancestor vault")
    codec = workers if relative.parts[0] == workers.ARCHIVE_ROOT else archive
    custody = codec.capture_custody(home)
    fd = os.open(staged, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise PermissionError("Browser import stage is not a regular file")
        # The private import stage was created by mkstemp. Only its mode changes,
        # never the ciphertext; final publication must retain this secret mode.
        os.fchmod(handle.fileno(), 0o600)
        codec.authenticate_ciphertext(handle, relative, custody=custody)
    from hermes_security.artifacts import _parent

    with _parent(home, relative, create=True) as parent:
        os.fsync(parent)  # a fresh recovery home also needs its secret directory at 0700
    return True


def restore_mode(target: Path, mode):
    return 0o600 if current_archive(Path(target.parent.name) / target.name) else mode


@contextlib.contextmanager
def selected_home(home: Path):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(home)
    try:
        yield
    finally:
        reset_hermes_home_override(token)


def profiles(home: Path):
    from hermes_cli import browser_profile_store as store
    from hermes_cli.browser_connect import _BROWSERS

    with selected_home(home):
        for slot in [archive.DEBUG_SLOT, *(archive.real_slot(browser.key) for browser in _BROWSERS)]:
            yield store.owned_profile(slot)


@contextlib.contextmanager
def restore_guard(home: Path, relative_members):
    """Hold existing profile locks until restore finishes; stale RAM must never undo a restore."""
    from hermes_cli import browser_profile_store as store
    from hermes_state.runtime_ownership import OwnershipConflict

    owners = set()
    worker_owners = set()
    for relative in relative_members:
        parts = Path(relative).parts
        if parts:
            candidate = home.joinpath(*parts[:2]) if parts[0] == "profiles" and len(parts) > 2 else home
            from hermes_security.browser_worker_pending import paths

            if paths(candidate):
                owners.add(candidate)
                worker_owners.add(candidate)
        if current_archive(relative):
            owner = home.joinpath(*parts[:2]) if parts[0] == "profiles" else home
            owners.add(owner)
            if browser_relative(relative).parts[0] == workers.ARCHIVE_ROOT:
                worker_owners.add(owner)
    with contextlib.ExitStack() as stack:
        try:
            for owner in sorted(owners):
                if owner in worker_owners:
                    from hermes_cli import browser_worker_store

                    with selected_home(owner):
                        for worker in browser_worker_store.resources_in_ram(owner):
                            stack.enter_context(browser_worker_store._lock(worker.lock_path, wait=0))
                            raise OwnershipConflict(
                                'Private browser state still exists in RAM. Close its owners and run '
                                'hermes secure-vault repair before import. No backup members were restored.')
                for profile in profiles(owner):
                    stack.enter_context(store._profile_lock(profile, wait=0.0))
                    if os.path.lexists(profile.path):
                        raise OwnershipConflict(
                            "Browser state still exists in RAM. Close that browser and Hermes, run "
                            "'hermes secure-vault repair' for this profile to save and retire it, then retry import. "
                            "No backup members were restored.")
        except (store.BrowserProfileError, VaultError, OSError) as exc:
            raise OwnershipConflict(f"Cannot safely inspect browser RAM state before restore: {exc}") from exc
        yield
