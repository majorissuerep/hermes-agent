"""Process-independent custody of a browser recording's RAM capture until it is sealed.

A recording's only plaintext lives in a STABLE owner-only directory on the proven RAM-only
protected base, ``hermes-browser-recording-<token>``, created by :func:`allocate` and never removed
by a context manager, destructor or ``atexit`` hook. The directory holds a keyless manifest (exact
destination name, owning home, a nonsecret same-vault fingerprint, the pinned directory inode, and
the recording process's PID/start-time identity) beside the capture. A durable encrypted locator
``artifacts/tool-results/recording-pending-<token>.json`` names the directory, so an explicit
``hermes secure-vault repair`` still finds a capture after its process ended or after
``security.protected_work_dir`` was later changed. Neither file carries keys, capabilities or
passphrases.

States: ``recording`` (allocated; the browser may still be writing) -> ``completed`` (the
caller acknowledged a clean ``record stop``; size, hash and inode pinned) -> ``published``
(sealed under ``artifacts/media/videos`` and authenticated back). Only a ``published`` capture is
retired: capture, then locator, then manifest and directory. Every failure before that keeps the
capture and the locator in place and says so.

Every authenticated read/publication uses the ORIGINAL custody captured by the caller
(``hermes_security.profile_binding.ProfileCustody``) and rechecks it; a later unlock is never adopted.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from hermes_platform.memory_protection import RamProof, ram_only_directory_proof
from hermes_security.errors import VaultError

logger = logging.getLogger(__name__)

PREFIX = "hermes-browser-recording-"
VIDEO_SUBDIR = ("artifacts", "media", "videos")
LOCATOR_SUBDIR = ("artifacts", "tool-results")
_NAME = re.compile(r"browser_recording_\d{8}T\d{6}Z_([0-9a-f]{32})\.webm")
_LOCATOR = re.compile(r"recording-pending-([0-9a-f]{32})\.json")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_MANIFEST = "recording.json"
_MANIFEST_BOUND = 16 * 1024
_CHUNK = 1024 * 1024
_FINGERPRINT_PURPOSE = "browser-recording:v1:same-vault-fingerprint"
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


class PendingRecordingError(RuntimeError):
    """A pending recording was not allocated, completed, saved or retired; the message says what
    was kept and where."""


def _prove(path: Path) -> RamProof:
    """RAM-proof seam for unit contracts; production always uses the live host proof."""
    return ram_only_directory_proof(path)


def _owner_identity() -> dict:
    """This native process's identity as recovery must later prove it ended."""
    from hermes_platform.process_identity import pid_namespace_id
    from hermes_platform.processes import get_process_start_time

    pid = os.getpid()
    return {"pid": pid, "start_time": get_process_start_time(pid), "pidns": pid_namespace_id(),
            "uid": os.getuid()}


@dataclass(frozen=True)
class CaptureLease:
    """One allocated recording. ``stage`` is the host RAM file the capture is (or is fetched) in."""
    home: Path
    workspace: Path
    stage: Path
    name: str
    identity: tuple[int, int]  # (st_dev, st_ino) of ``workspace`` pinned at allocation
    locator: Path
    vault: str  # nonsecret same-vault fingerprint
    owner: dict = field(hash=False)


def _binding(lease: CaptureLease) -> dict:
    """The keyless facts both the RAM manifest and the encrypted locator carry."""
    return {"version": 1, "home": str(lease.home), "vault": lease.vault, "name": lease.name,
            "workspace": str(lease.workspace), "workspace_id": list(lease.identity), "owner": lease.owner}


def _fingerprint(custody: Any) -> str:
    return hashlib.sha256(custody.derive_key(_FINGERPRINT_PURPOSE)).hexdigest()


def _require_custody(lease: CaptureLease, custody: Any) -> None:
    """The ORIGINAL custody is live, owns exactly ``lease.home`` and unlocks the same vault."""
    from hermes_security import artifacts

    artifacts._bound_custody(lease.home, custody)
    if not secrets.compare_digest(_fingerprint(custody), lease.vault):
        raise PendingRecordingError(
            f"the browser recording {lease.name} belongs to a different vault of this profile; "
            f"it was kept in protected RAM at {lease.stage}")


# --- Pinned RAM workspace and manifest ---------------------------------------------------------

@contextlib.contextmanager
def _pinned(lease: CaptureLease) -> Iterator[int]:
    """Open the allocated directory by exact path; re-prove inode, owner, mode and RAM backing."""
    if os.path.realpath(lease.workspace) != str(lease.workspace):
        raise PendingRecordingError(f"the recording workspace {lease.workspace} no longer resolves to itself")
    try:
        fd = os.open(lease.workspace, _DIR_FLAGS)
    except FileNotFoundError as exc:
        raise PendingRecordingError(
            f"its protected RAM workspace {lease.workspace} is absent (RAM was cleared, or that "
            "RAM directory is not mounted now)") from exc
    try:
        info = os.fstat(fd)
        if ((info.st_dev, info.st_ino) != tuple(lease.identity) or info.st_uid != os.getuid()
                or info.st_uid != lease.owner.get("uid") or stat.S_IMODE(info.st_mode) != 0o700):
            raise PendingRecordingError(f"the recording workspace {lease.workspace} was replaced or changed owner/mode")
        proof = _prove(lease.workspace)
        if not proof.proven:
            raise PendingRecordingError(f"the recording workspace {lease.workspace} is no longer proven RAM-only: "
                                        f"{proof.reason}")
        yield fd
    finally:
        os.close(fd)


def _read_manifest(fd: int) -> dict:
    try:
        manifest_fd = os.open(_MANIFEST, _FILE_FLAGS, dir_fd=fd)
    except FileNotFoundError as exc:
        raise PendingRecordingError("the recording manifest is missing") from exc
    with os.fdopen(manifest_fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or info.st_mode & 0o077):
            raise PendingRecordingError("the recording manifest is not an owner-only single-link regular file")
        raw = handle.read(_MANIFEST_BOUND + 1)
    try:
        manifest = json.loads(raw) if len(raw) <= _MANIFEST_BOUND else None
    except ValueError:
        manifest = None
    if not isinstance(manifest, dict):
        raise PendingRecordingError("the recording manifest is unreadable")
    return manifest


def _require_manifest(fd: int, lease: CaptureLease) -> dict:
    manifest = _read_manifest(fd)
    if any(manifest.get(key) != value for key, value in _binding(lease).items()):
        raise PendingRecordingError(f"the manifest in {lease.workspace} does not describe this recording")
    return manifest


def _write_manifest(fd: int, manifest: dict) -> None:
    """Atomic owner-0600 replacement inside the pinned directory."""
    temporary = f".{_MANIFEST}.{secrets.token_hex(8)}"
    temp_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                      0o600, dir_fd=fd)
    try:
        with os.fdopen(temp_fd, "wb") as handle:
            handle.write(json.dumps({**manifest, "updated": time.time()}, sort_keys=True).encode())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, _MANIFEST, src_dir_fd=fd, dst_dir_fd=fd)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=fd)
        raise


def _read_capture(fd: int, lease: CaptureLease, *, collect: bool) -> tuple[dict, Optional[bytes]]:
    """Bounded read of the owned single-link regular capture; refuses one that changes meanwhile."""
    from hermes_security.artifacts import _MAX_VIDEO_BYTES

    try:
        capture_fd = os.open(lease.name, _FILE_FLAGS, dir_fd=fd)
    except FileNotFoundError as exc:
        raise PendingRecordingError(f"the browser wrote no capture at {lease.stage}") from exc
    digest, chunks, size = hashlib.sha256(), [], 0
    with os.fdopen(capture_fd, "rb") as source:
        before = os.fstat(source.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_nlink != 1):
            raise PendingRecordingError(f"{lease.stage} is not an owned single-link regular file; it was kept")
        while chunk := source.read(_CHUNK):
            size += len(chunk)
            if size > _MAX_VIDEO_BYTES:
                raise PendingRecordingError(
                    f"{lease.stage} exceeds the {_MAX_VIDEO_BYTES // (1024 * 1024)} MiB video bound; it was kept")
            digest.update(chunk)
            if collect:
                chunks.append(chunk)
        after = os.fstat(source.fileno())
    if size != after.st_size or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise PendingRecordingError(f"{lease.stage} changed while it was read; it was kept")
    facts = {"sha256": digest.hexdigest(), "size": size, "id": [after.st_dev, after.st_ino]}
    return facts, (b"".join(chunks) if collect else None)


# --- Allocation --------------------------------------------------------------------------------

def _token(name: str) -> str:
    match = _NAME.fullmatch(name) if isinstance(name, str) else None
    if match is None:
        raise PendingRecordingError("a browser recording name is browser_recording_<UTC stamp>_<32 hex>.webm")
    return match.group(1)


def _retire_allocation(lease: CaptureLease) -> None:
    """Remove an allocation that never held a capture: only our manifest, then the empty directory."""
    try:
        with _pinned(lease) as fd:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(_MANIFEST, dir_fd=fd)
        _remove_directory(lease)
    except (OSError, PendingRecordingError) as exc:
        logger.warning("The empty browser recording allocation %s was left in protected RAM: %s",
                       lease.workspace, exc)


def _remove_directory(lease: CaptureLease) -> None:
    """``rmdir`` the exact pinned inode: refuses (never recursively deletes) anything left inside."""
    info = os.lstat(lease.workspace)
    if (info.st_dev, info.st_ino) != tuple(lease.identity):
        raise PendingRecordingError(f"the recording workspace {lease.workspace} was replaced")
    os.rmdir(lease.workspace)


def allocate(home: Path, custody: Any, name: str) -> CaptureLease:
    """Allocate the stable RAM capture for ``name`` and persist its encrypted locator.

    Raises :class:`PendingRecordingError` when recording must not start; an allocation whose
    locator could not be published is retired before the error (it never held a capture)."""
    from hermes_security import artifacts
    from hermes_security import protected_workspace as workspaces

    home, token = Path(home), _token(name)
    owner = _owner_identity()
    if not isinstance(owner.get("start_time"), int):
        raise PendingRecordingError("this process's start time cannot be pinned, so an unsaved recording "
                                    "could never be safely recovered; not recording")
    try:
        artifacts._bound_custody(home, custody)
        fingerprint = _fingerprint(custody)
        base = Path(os.path.realpath(workspaces.protected_workspace_base()))
        path = workspaces.stable_workspace_path(PREFIX + token, base)
        if os.path.lexists(path):
            raise PendingRecordingError(f"{path} already exists; a recording never adopts an existing directory")
        workspaces.ensure_stable_workspace(path, prove=_prove)
        info = os.lstat(path)
    except (VaultError, OSError, ValueError) as exc:
        raise PendingRecordingError(f"no protected RAM recording workspace ({exc})") from exc
    lease = CaptureLease(home, path, path / name, name, (info.st_dev, info.st_ino),
                         home.joinpath(*LOCATOR_SUBDIR) / f"recording-pending-{token}.json", fingerprint, owner)
    try:
        with _pinned(lease) as fd:
            if os.listdir(fd):
                raise PendingRecordingError(f"{path} was not empty when allocated")
            _write_manifest(fd, {**_binding(lease), "state": "recording", "created": time.time()})
        custody.check()
        artifacts.write_bytes(lease.locator, json.dumps(_binding(lease), sort_keys=True).encode(),
                              home=home, custody=custody)
    except (VaultError, OSError, ValueError, PendingRecordingError) as exc:
        _retire_allocation(lease)
        raise PendingRecordingError(f"the recording manifest or encrypted locator could not be stored ({exc}); "
                                    "the empty allocation was retired and nothing is recording") from exc
    return lease


# --- Completion, publication and retirement ----------------------------------------------------

def store_capture(lease: CaptureLease, custody: Any, data: bytes) -> None:
    """Store an outgoing capture through the pinned RAM directory, never a pathname transfer."""
    from hermes_security.artifacts import _MAX_VIDEO_BYTES, _write_all

    if len(data) > _MAX_VIDEO_BYTES:
        raise PendingRecordingError("The outgoing recording exceeds the video size bound")
    _require_custody(lease, custody)
    with _pinned(lease) as parent:
        manifest = _require_manifest(parent, lease)
        if manifest.get("state") != "recording":
            raise PendingRecordingError("Only an allocated recording can receive a capture")
        try:
            fd = os.open(lease.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=parent)
        except FileExistsError:
            _, existing = _read_capture(parent, lease, collect=True)
            if existing != data:
                raise PendingRecordingError("The earlier RAM transfer differs; both captures were retained") from None
            return
        with os.fdopen(fd, "wb") as output:
            _write_all(output.fileno(), data)
            os.fsync(output.fileno())


def completed(lease: CaptureLease, custody: Any) -> None:
    """Pin the capture after an ACKNOWLEDGED ``record stop``: size, hash and inode, state ``completed``.

    Never call this for a failed, unknown or forced stop; such a capture stays ``recording``."""
    _require_custody(lease, custody)
    with _pinned(lease) as fd:
        manifest = _require_manifest(fd, lease)
        capture, _ = _read_capture(fd, lease, collect=False)
        if manifest.get("state") == "completed" and manifest.get("capture") == capture:
            return
        if manifest.get("state") != "recording":
            raise PendingRecordingError(f"the recording {lease.name} is already {manifest.get('state')}")
        custody.check()
        _write_manifest(fd, {**manifest, "state": "completed", "capture": capture})


def _recorded_capture(manifest: dict, lease: CaptureLease) -> dict:
    state, capture = manifest.get("state"), manifest.get("capture")
    if state not in ("completed", "published") or not isinstance(capture, dict):
        raise PendingRecordingError(
            f"the recording was not stopped cleanly (state {state}); its incomplete capture stays in "
            f"protected RAM at {lease.stage} and was not saved")
    return capture


def _verify_destination(lease: CaptureLease, custody: Any, destination: Path, recorded: dict,
                        data: Optional[bytes]) -> None:
    """Authenticate the sealed copy under the original custody, compare it, then fsync its parent."""
    from hermes_security import artifacts

    custody.check()
    sealed = artifacts.read_bytes(destination, home=lease.home, custody=custody)
    same = (sealed == data if data is not None else
            len(sealed) == recorded.get("size")
            and secrets.compare_digest(hashlib.sha256(sealed).hexdigest(), str(recorded.get("sha256"))))
    if not same:
        raise PendingRecordingError(f"{destination} already holds a different recording; the RAM capture "
                                    f"was kept at {lease.stage}")
    with artifacts._parent(lease.home, Path(*VIDEO_SUBDIR, lease.name)) as parent:
        os.fsync(parent)


def _seal(fd: int, lease: CaptureLease, custody: Any, manifest: dict, recorded: dict) -> Path:
    from hermes_security import artifacts

    destination = lease.home.joinpath(*VIDEO_SUBDIR) / lease.name
    if manifest["state"] == "published":  # an earlier retirement was interrupted after verification
        _verify_destination(lease, custody, destination, recorded, None)
        return destination
    capture, data = _read_capture(fd, lease, collect=True)
    if capture != recorded:
        raise PendingRecordingError(f"the RAM capture {lease.stage} changed after it was completed; "
                                    "it was kept and not saved")
    custody.check()
    with contextlib.suppress(FileExistsError):  # an uncertain earlier attempt; authenticated below
        artifacts.write_bytes(destination, data, home=lease.home, custody=custody)
    _verify_destination(lease, custody, destination, recorded, data)
    _write_manifest(fd, {**manifest, "state": "published"})
    return destination


def _unlink_capture(fd: int, lease: CaptureLease, recorded: dict) -> None:
    try:
        info = os.stat(lease.name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if [info.st_dev, info.st_ino] != recorded.get("id"):
        raise PendingRecordingError(f"{lease.stage} was replaced after publication; it was kept")
    os.unlink(lease.name, dir_fd=fd)


def _unlink_locator(lease: CaptureLease, custody: Any) -> None:
    from hermes_security import artifacts

    relative = lease.locator.relative_to(lease.home)
    with artifacts._parent(lease.home, relative) as parent:
        try:
            before = os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        binding = json.loads(artifacts.read_bytes(lease.locator, home=lease.home, custody=custody))
        if binding != _binding(lease):
            raise PendingRecordingError("the recording locator now describes another allocation; it was kept")
        custody.check()
        after = os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise PendingRecordingError("the recording locator changed during cleanup; it was kept")
        os.unlink(relative.name, dir_fd=parent)
        os.fsync(parent)


def _retire(fd: int, lease: CaptureLease, recorded: dict, custody: Any) -> None:
    """Capture, then locator, then manifest; a ``published`` manifest marks every partial state."""
    _require_custody(lease, custody)
    _unlink_capture(fd, lease, recorded)
    _unlink_locator(lease, custody)
    custody.check()
    os.unlink(_MANIFEST, dir_fd=fd)


def publish_and_retire(lease: CaptureLease, custody: Any) -> tuple[Path, Optional[str]]:
    """Seal and authenticate a completed capture; ``(destination, cleanup problem or None)``.

    Raises before publication is verified (capture and locator kept). After it, a retirement
    failure is returned, not raised: the recording IS saved and a retry stays idempotent."""
    _require_custody(lease, custody)
    with _pinned(lease) as fd:
        manifest = _require_manifest(fd, lease)
        recorded = _recorded_capture(manifest, lease)
        destination = _seal(fd, lease, custody, manifest, recorded)
        try:
            _retire(fd, lease, recorded, custody)
        except (OSError, ValueError, VaultError, PendingRecordingError) as exc:
            return destination, f"{type(exc).__name__}: {exc}"
    try:
        custody.check()
        _remove_directory(lease)
    except (OSError, VaultError, PendingRecordingError) as exc:
        return destination, f"the empty RAM directory {lease.workspace} remains ({exc})"
    return destination, None


def publish(lease: CaptureLease, custody: Any) -> Path:
    """Seal a completed capture with the original custody; retire RAM and locator only after a
    verified, authenticated publication. Every failure before that raises and keeps both."""
    destination, problem = publish_and_retire(lease, custody)
    if problem:
        logger.warning("Browser recording saved encrypted at %s; its RAM/locator cleanup is incomplete "
                       "(%s). 'hermes secure-vault repair' finishes it.", destination, problem)
    return destination


def discard_empty(lease: CaptureLease, custody: Any) -> None:
    """Retire an allocation whose recording never started: no capture, or an empty one only.

    A nonempty (or unexpected) capture is kept for explicit recovery and raises."""
    _require_custody(lease, custody)
    with _pinned(lease) as fd:
        manifest = _require_manifest(fd, lease)
        extra = set(os.listdir(fd)) - {_MANIFEST}
        if manifest.get("state") != "recording" or extra - {lease.name}:
            raise PendingRecordingError(f"{lease.workspace} holds more than an unused allocation; it was kept")
        if lease.name in extra:
            info = os.stat(lease.name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_size or info.st_nlink != 1:
                raise PendingRecordingError(f"{lease.stage} holds capture data; it was kept for recovery")
            os.unlink(lease.name, dir_fd=fd)
        _unlink_locator(lease, custody)
        os.unlink(_MANIFEST, dir_fd=fd)
    _remove_directory(lease)


# --- Explicit recovery (``hermes_cli.browser_recording_repair``) --------------------------------

def locator_names(home: Path) -> list[str]:
    """Exact pending-recording locator names of ``home``; creates nothing."""
    from hermes_security import artifacts

    try:
        with artifacts._parent(home, Path(*LOCATOR_SUBDIR, "probe")) as parent:
            return sorted(entry.name for entry in os.scandir(parent) if _LOCATOR.fullmatch(entry.name))
    except FileNotFoundError:
        return []


def _valid_owner(owner: Any) -> bool:
    if not isinstance(owner, dict):
        return False
    pid, start, uid, pidns = (owner.get(key) for key in ("pid", "start_time", "uid", "pidns"))
    numbers = all(isinstance(value, int) and not isinstance(value, bool) for value in (pid, start, uid))
    return numbers and pid > 0 and start > 0 and (pidns is None or isinstance(pidns, str))


def _valid_pair(identity: Any) -> bool:
    return (isinstance(identity, list) and len(identity) == 2
            and all(isinstance(value, int) and not isinstance(value, bool) for value in identity))


def _valid_location(binding: dict, locator: Path) -> bool:
    """Exact recording name, locator name and RAM directory name all carry one token."""
    name, workspace = binding.get("name"), binding.get("workspace")
    match = _NAME.fullmatch(name) if isinstance(name, str) else None
    locator_match = _LOCATOR.fullmatch(locator.name)
    return (match is not None and locator_match is not None and match.group(1) == locator_match.group(1)
            and isinstance(workspace, str) and os.path.isabs(workspace)
            and Path(workspace).name == PREFIX + match.group(1))


def _lease_from_binding(home: Path, locator: Path, binding: Any) -> CaptureLease:
    if not isinstance(binding, dict) or binding.get("version") != 1 or binding.get("home") != str(home):
        raise PendingRecordingError("the pending-recording locator does not belong to this profile")
    vault, identity = binding.get("vault"), binding.get("workspace_id")
    if (not _valid_location(binding, locator) or not _valid_pair(identity) or not isinstance(vault, str)
            or not _HEX64.fullmatch(vault) or not _valid_owner(binding.get("owner"))):
        raise PendingRecordingError("the pending-recording locator is malformed")
    path, name = Path(binding["workspace"]), binding["name"]
    return CaptureLease(home, path, path / name, name, (identity[0], identity[1]), locator, vault,
                        dict(binding["owner"]))


def _owner_state(owner: dict) -> str:
    """``dead`` only when PID/start-time identity PROVES the recording process ended; else
    ``live`` or ``unknown`` (foreign/unstamped PID namespace, unreadable start time)."""
    from hermes_platform.process_identity import holder_pid_checkable
    from hermes_platform.processes import get_process_start_time, pid_exists, process_start_times_agree

    pid = owner["pid"]
    if not holder_pid_checkable(f"pid={pid}" + (f":pidns={owner['pidns']}" if owner.get("pidns") else "")):
        return "unknown"
    if not pid_exists(pid):
        return "dead"
    current = get_process_start_time(pid)
    if current is None or current <= 0:
        return "unknown"
    return "live" if process_start_times_agree(current, owner["start_time"]) else "dead"


def recover(home: Path, locator_name: str, custody: Any) -> tuple[Path, Optional[str]]:
    """Save one pending recording of ``home`` whose owning process provably ended.

    ``custody`` is the explicit repair's own fresh custody of exactly ``home``. The locator is
    authenticated before its RAM path is trusted. Raises :class:`PendingRecordingError` (or a
    vault/OS error) explaining why the capture was kept; nothing is deleted on refusal."""
    from hermes_security import artifacts

    locator = home.joinpath(*LOCATOR_SUBDIR) / locator_name
    custody.check()
    raw = artifacts.read_bytes(locator, home=home, custody=custody)
    try:
        binding = json.loads(raw)
    except ValueError as exc:
        raise PendingRecordingError("the pending-recording locator is unreadable") from exc
    lease = _lease_from_binding(home, locator, binding)
    _require_custody(lease, custody)
    state = _owner_state(lease.owner)
    if state != "dead":
        why = "is still running" if state == "live" else "cannot be proven to have ended"
        raise PendingRecordingError(
            f"its recording process (PID {lease.owner['pid']}) {why}; the capture stays in protected RAM "
            f"at {lease.stage}. Close that Hermes session and run repair again")
    return publish_and_retire(lease, custody)
