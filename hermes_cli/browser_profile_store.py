"""Hermes-owned browser profiles: active copy in proven RAM, durable copy as an encrypted archive.

Two profile families are owned by a Hermes home: the real-profile snapshot copy
(``browser.use_real_profile``, one per default browser) and the ``/browser connect`` debug profile.
Chromium needs real, writable files, so the ACTIVE copy lives in a named directory on the proven
RAM-only protected workspace base; the DURABLE copy is an authenticated encrypted archive under
``HERMES_HOME/browser-profile/`` (``hermes_security.browser_profile_archive``). Nothing here writes
plaintext profile state to persistent storage, and nothing falls back when RAM, the vault, or a
readable archive is missing — the caller gets an actionable :class:`BrowserProfileError`.

Lifecycle (all transitions under a per-profile lock file in the RAM base that is never unlinked):

- ``prepare`` restores the archive into a fresh RAM stage and publishes it, or reuses an existing
  quiescent copy of the same identity (checkpointing unsaved changes first), then claims it for
  an imminent browser launch.
- ``record_launch`` binds the launched browser's PID/start-time to the copy.
- ``settle`` (browser-exit, janitor tick, interpreter exit) persists a quiescent copy with the
  custody captured by the original request and retires the RAM copy only after a verified save.

A copy is busy while a launch claim is fresh, while the Hermes-launched browser is alive, or while
Chromium's own ``SingletonLock`` names a live local process. Busy copies are never persisted,
replaced or removed. A failed save keeps both the RAM copy and the previous archive.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import secrets
import socket
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from hermes_platform.memory_protection import RamProof, ram_only_directory_proof
from hermes_security import browser_profile_archive as archive
from hermes_platform.memory_capacity import capacity_budget as _capacity
from hermes_security.errors import VaultError

logger = logging.getLogger(__name__)

_NAME_PREFIX = "hermes-browser-profile-"
_LAUNCH_GRACE_S = 120.0
_LOCK_WAIT_S = 60.0
_RAM_MARGIN = 64 * 1024 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class BrowserProfileError(RuntimeError):
    """The owned browser profile cannot be used safely right now; the message says how to fix it."""


class _LockBusy(BrowserProfileError):
    """Another process holds the profile lock (preparing, saving, or repairing it)."""


def _prove(path: Path) -> RamProof:
    """RAM-proof seam for unit contracts; production always uses the live host proof."""
    return ram_only_directory_proof(path)


@dataclass(frozen=True)
class OwnedProfile:
    home: Path
    slot: str
    base: Path

    @property
    def name(self) -> str:
        seed = f"hermes-browser-profile|v1|{self.home}|{self.slot}".encode()
        return _NAME_PREFIX + hashlib.sha256(seed).hexdigest()[:32]

    @property
    def path(self) -> Path:
        from hermes_security.protected_workspace import stable_workspace_path

        return stable_workspace_path(self.name, self.base)

    @property
    def lock_path(self) -> Path:
        return self.base / f"{self.name}.lock"


def owned_profile(slot: str) -> OwnedProfile:
    """The owned profile ``slot`` of the active home. Pure: resolves paths, creates nothing."""
    from hermes_constants import get_hermes_home
    from hermes_security.protected_workspace import protected_workspace_base

    try:
        base = Path(os.path.realpath(protected_workspace_base()))
    except (VaultError, OSError) as exc:  # includes ProtectedWorkspaceUnavailable and a locked config
        raise BrowserProfileError(_ram_hint(str(exc))) from exc
    return OwnedProfile(Path(get_hermes_home()).expanduser().resolve(), slot, base)


def _ram_hint(reason: str) -> str:
    return (f"Hermes keeps active browser profiles only in proven RAM ({reason}). Set "
            "security.protected_work_dir to an owner-only ramfs directory (or use a RAM-only "
            "XDG_RUNTIME_DIR with no active swap) and retry.")


def _require_ram(base: Path) -> None:
    proof = _prove(base)
    if not proof.proven:
        raise BrowserProfileError(_ram_hint(f"{base} is not on a RAM-only mount: {proof.reason}"))


def _capture(home: Path):
    try:
        return archive.capture_custody(home)
    except (VaultError, PermissionError) as exc:
        raise BrowserProfileError(
            "Hermes stores browser profiles encrypted, and this profile's vault is not available "
            f"({exc}). Unlock it (or run 'hermes secure-vault migrate' once), then retry.") from exc


# --- Locking ----------------------------------------------------------------------------------

_held = threading.local()


@contextlib.contextmanager
def _profile_lock(profile: OwnedProfile, *, wait: float = _LOCK_WAIT_S) -> Iterator[None]:
    """Cross-process exclusive lock, re-entrant per thread. The lock inode is never unlinked."""
    from hermes_platform.processes import release_file_lock, try_acquire_file_lock
    from hermes_state.runtime_ownership import _open_lock

    held = getattr(_held, "locks", None)
    if held is None:
        held = _held.locks = {}
    key = str(profile.lock_path)
    if key in held:
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    _require_ram(profile.base)
    handle = _open_lock(profile.lock_path)
    try:
        deadline = time.monotonic() + wait
        while not try_acquire_file_lock(handle):
            if time.monotonic() >= deadline:
                raise _LockBusy("Another Hermes process is saving or preparing this browser profile; retry shortly.")
            time.sleep(0.1)
        held[key] = 1
        try:
            yield
        finally:
            del held[key]
            release_file_lock(handle)
    finally:
        handle.close()


# --- Binding metadata (RAM only) --------------------------------------------------------------

def _own_process() -> dict:
    from hermes_platform.processes import get_process_start_time

    return {"pid": os.getpid(), "start_time": get_process_start_time(os.getpid())}


def _read_binding(directory: Path) -> dict | None:
    try:
        fd = os.open(directory / archive.RUNTIME_BINDING_NAME, os.O_RDONLY | _NOFOLLOW)
    except (FileNotFoundError, NotADirectoryError):
        return None
    with os.fdopen(fd, "rb") as handle:
        raw = handle.read(64 * 1024 + 1)
    try:
        binding = json.loads(raw)
    except ValueError:
        return None
    return binding if isinstance(binding, dict) else None


def _write_binding(directory: Path, binding: dict) -> None:
    temporary = directory / f"{archive.RUNTIME_BINDING_NAME}.{secrets.token_hex(8)}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(json.dumps({**binding, "updated": time.time()}, sort_keys=True).encode())
        os.replace(temporary, directory / archive.RUNTIME_BINDING_NAME)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def _owned_binding(profile: OwnedProfile, path: Path) -> dict:
    binding = _read_binding(path) if path.is_dir() and not path.is_symlink() else None
    if binding is None or binding.get("slot") != profile.slot or binding.get("home") != str(profile.home):
        raise BrowserProfileError(
            f"{path} exists without Hermes ownership metadata for this profile. Close any browser "
            "using it and move that directory aside, then retry.")
    return binding


def _singleton_owner(directory: Path) -> str | None:
    """Why Chromium's own singleton lock says a browser holds ``directory``, else None."""
    from hermes_platform.processes import pid_exists

    try:
        target = os.readlink(directory / "SingletonLock")
    except FileNotFoundError:
        return None
    except OSError:
        return "its browser lock cannot be inspected"
    host, separator, pid = target.rpartition("-")
    if not separator or not pid.isdigit():
        return "its browser lock is unreadable"
    if host != socket.gethostname():
        return "a browser on another host holds it"
    return "a browser is still running on it" if pid_exists(int(pid)) else None


def _busy_reason(directory: Path, binding: dict, *, own_claim_busy: bool) -> str | None:
    from hermes_platform.processes import process_record_is_live

    claimed = binding.get("claimed_at")
    if isinstance(claimed, (int, float)) and time.time() - claimed < _LAUNCH_GRACE_S:
        if own_claim_busy or binding.get("claimed_by") != _own_process():
            return "a browser launch on it is in progress"
    if process_record_is_live(binding.get("launcher")):
        return "a browser started by Hermes is still running on it"
    return _singleton_owner(directory)


# --- Leases: this process's captured custody per profile ---------------------------------------

@dataclass
class _Lease:
    """One request's claim on a RAM copy: its captured custody and the token written into the
    copy's binding. A lease acts only while the binding still names its token (no other request
    re-claimed the copy) and its original custody is live (never a later unlock)."""
    profile: OwnedProfile
    custody: Any
    token: str
    directory_identity: tuple[int, int]
    process: Any = None


_LEASES: dict[tuple[Path, str], _Lease] = {}
_LEASES_LOCK = threading.Lock()


def _remember(profile: OwnedProfile, custody, token: str) -> _Lease:
    identity = _directory_identity(profile.path)
    with _LEASES_LOCK:
        lease = _LEASES[(profile.home, profile.slot)] = _Lease(profile, custody, token, identity)
    return lease


def _directory_identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise BrowserProfileError("The active browser profile is not its private owned directory")
    return info.st_dev, info.st_ino


def _require_lease_directory(lease: _Lease) -> None:
    if _directory_identity(lease.profile.path) != lease.directory_identity:
        raise BrowserProfileError("The active browser profile directory was replaced; every copy was retained")


def _forget(profile: OwnedProfile, lease: _Lease | None = None) -> None:
    """Drop the lease for ``profile`` (only ``lease`` itself when given, never a newer one)."""
    key = (profile.home, profile.slot)
    with _LEASES_LOCK:
        if lease is None or _LEASES.get(key) is lease:
            _LEASES.pop(key, None)


def _custody_live(custody) -> bool:
    """Checked immediately before each mutation a lease performs; a revoked custody stays revoked."""
    try:
        custody.check()
    except VaultError:
        return False
    return True


# --- Persist ----------------------------------------------------------------------------------

def _archive_relative(binding: dict) -> PurePosixPath:
    relative = PurePosixPath(str(binding.get("archive") or ""))
    if archive.browser_state_kind(relative) != "archive":
        raise BrowserProfileError("The RAM browser profile names an invalid archive location")
    return relative


def _persist_locked(profile: OwnedProfile, custody, path: Path, binding: dict) -> str | None:
    """Seal ``path``; return None or an unconfirmed-save reason, retaining its RAM copy."""
    if binding.get("consent_revoked"):
        return "real-profile consent was turned off; this copy is only ever discarded"
    custody.check()
    try:
        summary = archive.write_archive(
            profile.home, _archive_relative(binding), path, custody=custody, slot=profile.slot,
            identity=binding.get("identity_doc") or {}, digest=str(binding.get("identity")))
    except (archive.ArchiveError, VaultError, OSError, ValueError, BrowserProfileError) as exc:
        reason = f"{type(exc).__name__}: {exc}"
        if _custody_live(custody):
            _write_binding(path, {**binding, "persisted": False, "last_error": reason})
        logger.warning("browser profile %s save was not confirmed (RAM copy retained): %s",
                       profile.slot, reason)
        return reason
    custody.check()
    _write_binding(path, {**binding, "persisted": True, "last_error": None, "persisted_at": time.time()})
    logger.info("browser profile %s saved encrypted (%d entries, %d bytes)", profile.slot,
                summary.entries, summary.bytes)
    return None


# --- Prepare / restore ------------------------------------------------------------------------

def _legacy_root(profile: OwnedProfile) -> Path:
    if profile.slot == archive.DEBUG_SLOT:
        return profile.home / archive.LEGACY_DEBUG_ROOT
    return profile.home / archive.ARCHIVE_ROOT / profile.slot.removeprefix(archive.REAL_SLOT_PREFIX)


def _refuse_legacy(profile: OwnedProfile) -> None:
    from hermes_constants import display_hermes_home

    legacy = _legacy_root(profile)
    if os.path.lexists(legacy):
        shown = f"{display_hermes_home()}/{legacy.relative_to(profile.home).as_posix()}"
        raise BrowserProfileError(
            f"An unencrypted browser profile from an older Hermes is still at {shown}. Close every "
            "Hermes session and that browser, run 'hermes secure-vault repair' to move it into "
            "encrypted storage, then retry.")


def _require_capacity(base: Path, needed: int) -> None:
    budget = _capacity(base, proof=_prove(base))
    if budget.available_bytes is None:
        raise BrowserProfileError(
            f"The protected RAM budget is unknown ({budget.reason}). Check the readable kernel and "
            "cgroup memory facts and retry. The encrypted browser profile was left unchanged.")
    free = budget.available_bytes
    if free < needed + _RAM_MARGIN:
        raise BrowserProfileError(
            f"The protected RAM budget has {free // (1024 * 1024)} MiB free, too little to restore "
            f"this browser profile (about {needed // (1024 * 1024)} MiB). Free RAM workspace space or "
            "raise the applicable memory limit, then retry. The encrypted profile "
            "was left unchanged.")


def _restore_or_create(profile: OwnedProfile, custody, binding: dict) -> Path:
    from hermes_security.protected_workspace import (
        ensure_stable_workspace, fresh_sibling_workspace, remove_stable_workspace)

    relative = _archive_relative(binding)
    size = archive.archive_ciphertext_size(profile.home, relative)
    stage = fresh_sibling_workspace(profile.path, "restore", prove=_prove)
    try:
        if size is not None:
            _require_capacity(profile.base, size)
            archive.replay_archive(profile.home, relative, custody=custody, slot=profile.slot,
                                   digest=binding["identity"], target=stage)
        _write_binding(stage, {**binding, "persisted": size is not None, "restored": size is not None})
        os.rename(stage, profile.path)
    except (archive.ArchiveError, VaultError, OSError) as exc:
        remove_stable_workspace(stage)
        raise BrowserProfileError(
            f"The encrypted browser profile could not be restored ({type(exc).__name__}: {exc}). It "
            "was left unchanged; Hermes did not start an empty profile in its place.") from exc
    except BaseException:
        remove_stable_workspace(stage)
        raise
    return ensure_stable_workspace(profile.path, prove=_prove)


def _reuse_or_retire(profile: OwnedProfile, custody, digest: str) -> bool:
    """Existing RAM copy: True when it is reusable for ``digest``; retired (after a save) otherwise."""
    from hermes_security.protected_workspace import ensure_stable_workspace, remove_stable_workspace

    path = profile.path
    binding = _owned_binding(profile, path)
    busy = _busy_reason(path, binding, own_claim_busy=False)
    if busy:
        raise BrowserProfileError(
            f"The Hermes browser profile copy is in use ({busy}). Close that browser and retry; "
            "Hermes never rewrites a profile under a running browser.")
    ensure_stable_workspace(path, prove=_prove)
    if binding.get("consent_revoked"):
        custody.check()
        remove_stable_workspace(path)  # consent-off authorized deleting exactly this copy
        return False
    failure = None if binding.get("persisted") else _persist_locked(profile, custody, path, binding)
    if failure and getattr(custody, "revoked", False):
        raise BrowserProfileError(
            f"This request's vault access ended before the RAM browser profile was saved ({failure}). "
            "It was kept unchanged; unlock the vault and retry.")
    if binding.get("identity") == digest:
        # Reused even when the checkpoint failed: the RAM copy is the newest state and the previous
        # archive is intact. The binding keeps persisted=False plus last_error, so status reports it.
        return True
    if failure:
        raise BrowserProfileError(
            "The RAM browser profile for a different source profile or account could not be saved "
            f"({failure}); it was kept and nothing was switched.")
    custody.check()
    remove_stable_workspace(path)
    return False


def _materialize(profile: OwnedProfile, custody, identity: dict, token: str) -> Path:
    digest = archive.identity_digest(custody, profile.slot, identity)  # checks custody
    binding = {"format": 1, "home": str(profile.home), "slot": profile.slot, "identity": digest,
               "identity_doc": identity, "archive": archive.archive_relative(profile.slot, digest).as_posix()}
    _refuse_legacy(profile)
    reuse = os.path.lexists(profile.path) and _reuse_or_retire(profile, custody, digest)
    path = profile.path if reuse else _restore_or_create(profile, custody, binding)
    # Claimed BEFORE the caller writes or launches: no other process saves or retires it meanwhile.
    custody.check()
    _write_binding(path, {**_owned_binding(profile, path), "persisted": False, "claimed_at": time.time(),
                          "claimed_by": _own_process(), "launcher": None, "lease": token})
    return path


@contextlib.contextmanager
def prepare(profile: OwnedProfile, identity: dict) -> Iterator[Path]:
    """Yield the active RAM copy for ``identity`` under the profile lock, claimed for a launch.

    The custody of THIS request is captured first and kept for later saves of this copy.
    """
    with contextlib.ExitStack() as stack:
        try:
            custody, token = _capture(profile.home), secrets.token_hex(16)
            stack.enter_context(_profile_lock(profile))
            path = _materialize(profile, custody, identity, token)
        except (VaultError, OSError) as exc:
            raise BrowserProfileError(
                f"The Hermes browser profile could not be prepared ({type(exc).__name__}: {exc}). "
                "Existing copies were left unchanged.") from exc
        _remember(profile, custody, token)
        try:
            yield path
        except BaseException:
            with contextlib.suppress(OSError, BrowserProfileError, VaultError):
                custody.check()
                _write_binding(path, {**_owned_binding(profile, path), "claimed_at": None})
            raise


def adopt(profile: OwnedProfile) -> None:
    """Re-attached to a browser still running on the copy: save it with THIS request's custody
    once that browser exits (it is not ours to terminate)."""
    custody, token = _capture(profile.home), secrets.token_hex(16)
    with _profile_lock(profile):
        binding = _owned_binding(profile, profile.path)
        custody.check()
        _write_binding(profile.path, {**binding, "lease": token})
        _remember(profile, custody, token)


def record_launch(profile: OwnedProfile, process) -> None:
    """Bind a browser this process launched on the copy (PID + start time); clears the claim."""
    from hermes_platform.processes import get_process_start_time

    with _LEASES_LOCK:
        lease = _LEASES.get((profile.home, profile.slot))
    if lease is None:
        raise BrowserProfileError("The browser launch has no owning profile request")
    lease.custody.check()
    pid = getattr(process, "pid", None)
    with _profile_lock(profile):
        _require_lease_directory(lease)
        binding = _owned_binding(profile, profile.path)
        if binding.get("lease") != lease.token:
            raise BrowserProfileError("Another request now owns this browser profile launch")
        launcher = {"pid": pid, "start_time": get_process_start_time(pid)} if isinstance(pid, int) else None
        lease.custody.check()
        _write_binding(profile.path, {**binding, "claimed_at": None, "launcher": launcher, "persisted": False})
    lease.process = process


# --- Settle: save and retire at quiescent boundaries -------------------------------------------

def _settle_lease(lease: _Lease) -> str:
    from hermes_security.protected_workspace import remove_stable_workspace

    profile, path = lease.profile, lease.profile.path
    if not _custody_live(lease.custody):
        _forget(profile, lease)
        return "revoked"
    with _profile_lock(profile, wait=0.0):
        if not os.path.lexists(path):
            _forget(profile, lease)
            return "absent"
        _require_lease_directory(lease)
        binding = _owned_binding(profile, path)
        if binding.get("lease") != lease.token:
            _forget(profile, lease)  # another request re-claimed the copy; it owns the save now
            return "superseded"
        if not _custody_live(lease.custody):
            _forget(profile, lease)  # never resumes under a later unlock; a new request saves it
            return "revoked"
        if lease.process is not None and lease.process.poll() is not None:
            binding = {**binding, "launcher": None, "claimed_at": None}
            _write_binding(path, binding)
        if _busy_reason(path, binding, own_claim_busy=True):
            return "busy"
        if binding.get("consent_revoked"):
            lease.custody.check()
            remove_stable_workspace(path)  # consent-off authorized deleting exactly this copy
            _forget(profile, lease)
            return "discarded"
        if not binding.get("persisted") and _persist_locked(profile, lease.custody, path, binding):
            if lease.custody.revoked:
                _forget(profile, lease)
                return "revoked"
            return "unsaved"
        if not _custody_live(lease.custody):
            _forget(profile, lease)  # saved, but retiring the copy needs the original custody too
            return "revoked"
        remove_stable_workspace(path)
        _forget(profile, lease)
        return "saved"


def settle(slot_prefix: str = "", *, home: Path | None = None) -> dict[str, str]:
    """Save original leases for quiescent copies, optionally restricted to one exact home."""
    selected_home = Path(home).expanduser().resolve() if home is not None else None
    with _LEASES_LOCK:
        leases = [lease for (owner, slot), lease in _LEASES.items()
                  if slot.startswith(slot_prefix) and (selected_home is None or owner == selected_home)]
    outcome = {}
    for lease in leases:
        try:
            outcome[lease.profile.slot] = _settle_lease(lease)
        except _LockBusy:
            outcome[lease.profile.slot] = "busy"
        except (BrowserProfileError, OSError, VaultError) as exc:
            outcome[lease.profile.slot] = f"error: {exc}"
            logger.warning("browser profile %s could not be settled: %s", lease.profile.slot, exc)
    return outcome


# --- Consent-off discard ----------------------------------------------------------------------

def discard_real_profiles(browsers) -> dict[str, list[str]]:
    """Consent revoked: delete real-profile archives and quiescent copies; never a live browser's.

    Archives are deleted only after every real-profile lock is held, so no
    concurrent save republishes one. An unavailable lock retains the archives
    and reports that cleanup must be repeated. A busy copy under our lock is
    marked for discard at its next settle or prepare.
    The debug-profile archive and the operator's own browser profile are untouched.
    """
    from hermes_constants import get_hermes_home

    import shutil

    home = Path(get_hermes_home()).expanduser().resolve()
    custody = _capture(home)
    report: dict[str, list[str]] = {"removed": [], "retained": []}
    with contextlib.ExitStack() as locks:
        acquired = True
        for browser in browsers:
            acquired = _discard_ram_copy(browser, report, locks, custody) and acquired
        for path in archive.archive_paths(home):
            if path.name.startswith("real-"):
                if not acquired:
                    report["retained"].append(
                        f"{archive.ARCHIVE_ROOT}/{path.name} (profile lock unavailable; repeat cleanup after closing the browser)")
                    continue
                custody.check()
                path.unlink()
                report["removed"].append(f"{archive.ARCHIVE_ROOT}/{path.name}")
    for root in archive.legacy_profile_roots(home):
        if root.parent.name != archive.ARCHIVE_ROOT:
            continue
        shown = f"{archive.ARCHIVE_ROOT}/{root.name}"
        owner = _singleton_owner(root)
        if not owner:
            custody.check()
            shutil.rmtree(root, ignore_errors=True)
        if owner or os.path.lexists(root):
            report["retained"].append(f"{shown} ({owner or 'could not be removed completely'})")
        else:
            report["removed"].append(shown)
    with contextlib.suppress(OSError):
        (home / archive.ARCHIVE_ROOT).rmdir()  # only when empty
    return report


def _discard_ram_copy(browser: str, report: dict[str, list[str]], locks: contextlib.ExitStack, custody) -> bool:
    """Take (and keep, via ``locks``) this browser's profile lock, then remove its quiescent copy."""
    from hermes_security.protected_workspace import remove_stable_workspace

    try:
        profile = owned_profile(archive.real_slot(browser))
    except BrowserProfileError as exc:
        report["retained"].append(f"RAM copy for {browser} ({exc}; repeat cleanup after restoring the workspace)")
        return False
    try:
        custody.check()
        locks.enter_context(_profile_lock(profile))
        if not os.path.lexists(profile.path):
            return True
        binding = _owned_binding(profile, profile.path)
        busy = _busy_reason(profile.path, binding, own_claim_busy=True)
        if busy:
            custody.check()
            _write_binding(profile.path, {**binding, "consent_revoked": True, "persisted": False})
            busy += "; it is discarded, never saved, once that browser exits"
        else:
            custody.check()
            remove_stable_workspace(profile.path)
            _forget(profile)
    except (BrowserProfileError, OSError, VaultError) as exc:
        report["retained"].append(f"RAM copy for {browser} ({exc}; repeat cleanup after closing the browser)")
        return False
    if busy:
        report["retained"].append(f"RAM copy for {browser} ({busy})")
    else:
        report["removed"].append(f"RAM copy for {browser}")
    return True


# --- Status -----------------------------------------------------------------------------------

def _slot_label(slot: str) -> str:
    if slot == archive.DEBUG_SLOT:
        return "debug browser profile (/browser connect)"
    return f"real-profile copy ({slot.removeprefix(archive.REAL_SLOT_PREFIX)})"


def _active_line(profile: OwnedProfile) -> str | None:
    if not os.path.lexists(profile.path):
        return None
    binding = _read_binding(profile.path)
    if binding is None or binding.get("slot") != profile.slot:
        return f"{_slot_label(profile.slot)}: RAM directory without Hermes metadata at {profile.path}"
    busy = _busy_reason(profile.path, binding, own_claim_busy=True)
    state = "saved" if binding.get("persisted") else "unsaved changes held only in RAM"
    detail = f"; last save failed: {binding['last_error']}" if binding.get("last_error") else ""
    return f"{_slot_label(profile.slot)}: {state}{'; ' + busy if busy else ''}{detail}"


def status_lines(home: Path, browsers) -> list[str]:
    """Concise durable/volatile/legacy facts. Reads metadata only: no decryption, no creation."""
    home = Path(home).expanduser().resolve()
    archives = archive.archive_paths(home)
    total = sum(path.stat().st_size for path in archives)
    lines = [f"Browser profiles : {len(archives)} encrypted archive(s), {total // (1024 * 1024)} MiB"]
    try:
        slots = [archive.DEBUG_SLOT, *(archive.real_slot(b) for b in browsers)]
        lines += [f"  active (RAM)   : {line}" for line in filter(None, (_active_line(owned_profile(s)) for s in slots))]
    except BrowserProfileError as exc:
        lines.append(f"  active (RAM)   : unavailable — {exc}")
    legacy = [root.relative_to(home).as_posix() for root in archive.legacy_profile_roots(home)]
    if legacy:
        lines.append(f"  legacy plaintext: {', '.join(legacy)} — close Hermes and that browser, then run "
                     "'hermes secure-vault repair'")
    return lines
