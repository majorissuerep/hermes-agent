"""Explicit human repair of one selected outgoing target; never part of offline repair.

A new request owns the effects. Persisted owner/selection facts identify data, never
carry authority: old local owners must be proven ended and target RAM must still
match its exact original generation and allocation nonce. Partial/unknown state
and unrelated captures remain retained; no automatic reconnect or force close.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from hermes_security import artifacts, browser_profile_archive as archive
from tools import browser_outgoing_profile as profiles
from tools import browser_outgoing_state as state, browser_outgoing_transfer as transfer


@contextmanager
def _owner_admission(home, digest, reference):
    from tools.browser_recording_pending import _owner_state
    import os

    owner = reference["owner"]
    if owner["uid"] != os.getuid():
        raise state.OutgoingProfileError("Original outgoing browser owner is foreign; RAM retained")
    if _owner_state(owner) == "dead":
        yield None
        return
    resident = profiles.ended_resident(home, digest, reference)
    with profiles.environment_lock(resident.env):
        profiles.ended_resident(home, digest, reference)  # recheck after waiting for in-flight effects
        profiles.require_saved_recordings(resident.env)
        yield resident


def _captured_env(reference, env):
    """Use only the authenticated original backend ID, never a new replacement container."""
    backend = reference["backend"]
    if backend == "ssh":
        return env
    from tools.environments.captured_execution import capture_execution
    origin = reference["origin"]
    value = origin.get("container") if backend == "docker" else origin.get("instance")
    return capture_execution(env, backend, value, lock=profiles.environment_lock(env))


def _runtime(reference, env):
    from tools.browser_terminal import BrowserRuntime
    from tools.environments.streams import exec_prefix
    from hermes_platform.remote_identity import RuntimeIdentity

    recorded = reference["runtime"]
    directories = tuple(tuple(pair) for pair in recorded["directories"])
    root = directories[0][0]
    children = dict((Path(path).name, path) for path, _ in directories[1:])
    expected = {name: root + "/" + name for name in ("profile", "captures", "sockets", "tmp")}
    if children != expected:
        raise state.OutgoingProfileError("Original outgoing directory layout is invalid; retained")
    prefix = exec_prefix(env, interactive=False)
    if not prefix:
        raise state.OutgoingProfileError("Original outgoing transport is unavailable; retained")
    return BrowserRuntime(root, **children, client_identity=tuple(prefix),
                          generation=RuntimeIdentity(recorded["epoch"], directories))


def _recover(home, digest, reference, selected_env, *, resident=None):
    from tools import browser_terminal

    if reference["backend"] != browser_terminal.terminal_backend():
        raise state.OutgoingProfileError("Select the original outgoing backend before explicit repair; retained")
    env = _captured_env(reference, selected_env)
    runtime = _runtime(reference, env)
    lease = profiles._new_lease("explicit-recovery", {}, env, runtime, "")
    if lease.digest != digest or lease.identity != reference["identity"]:
        raise state.OutgoingProfileError("Selected target/account differs from original recovery identity; retained")
    lease.token, lease.bound, lease.closed = reference["lease"], True, True
    legacy = reference.get("legacy_path")
    if legacy is not None:
        if legacy != profiles._legacy_path(lease):
            raise state.OutgoingProfileError("Legacy source path differs from its original selected identity; retained")
        lease.legacy_path, lease.bound = legacy, False
    with state.locked(home, digest, lease.base):
        if state.read(home, digest, lease.custody) != reference:
            raise state.OutgoingProfileError("Outgoing recovery reference changed; retained")
        if not transfer.inspect(lease, path=runtime.captures, stale=True):
            raise state.OutgoingProfileError(f"Unrelated captures remain at {runtime.captures}; retained separately")
        if resident is not None and not resident.closed and reference["phase"] == "active":
            name = profiles.exclusive_resident(resident)
            transfer.close_ended_browser(lease, name)
            resident.closed = True
        if reference["phase"] == "restoring":
            _restoring(lease, reference)
        else:
            profiles._checkpoint(lease, stale=True, path=legacy)
    return lease.relative.as_posix()


def _restoring(lease, reference):
    """An interrupted restore can retire only a full match to its existing verified archive."""
    recovery = reference.get("recovery")
    if recovery is None:
        if not transfer.inspect(lease, stale=True):
            raise state.OutgoingProfileError("Unconfirmed initial profile work remains; no previous checkpoint can cover it")
        profiles._checkpoint(lease, stale=True)
        return
    checked = archive.replay_archive(lease.home, lease.relative, custody=lease.custody,
                                      slot=lease.slot, digest=lease.digest)
    if asdict(checked) != recovery:
        raise state.OutgoingProfileError("Original restore checkpoint changed; every copy retained")
    with profiles.workspace(lease, "outgoing-browser-restore-recovery") as ram:
        _tree, actual, raw_hash, maximum = transfer.download(lease, ram, stale=True)
        if actual != checked:
            raise state.OutgoingProfileError(
                f"Interrupted restore at {lease.runtime.profile} is incomplete; verified archive retained at "
                f"{lease.relative}. No target bytes were deleted or overwritten")
        transfer.retire(lease, raw_hash, maximum, stale=True)
        state.remove(lease.home, lease.digest, lease.custody, lease.token)


def recover(home: Path) -> dict:
    """Only this explicit operator action may connect to the selected configured backend."""
    from hermes_security.session_custody import SessionCustody
    from tools import browser_terminal

    home = home.resolve()
    custody = SessionCustody.capture(home, resource=artifacts._bound_custody(home))
    from hermes_security.operation_custody import guarded
    custody = guarded(custody)
    report = {"saved": [], "retained": []}
    for digest, reference in state.references(home, custody):
        try:
            if reference is None:
                continue
            with _owner_admission(home, digest, reference) as resident:
                if reference["backend"] != browser_terminal.terminal_backend():
                    raise state.OutgoingProfileError("Select the original configured backend to recover this reference")
                env = browser_terminal.terminal_environment(create=True)
                report["saved"].append(_recover(home, digest, reference, env, resident=resident))
                if resident is not None:
                    profiles.forget_settled(resident)
        except (RuntimeError, OSError, ValueError) as exc:
            report["retained"].append(f"{state.relative(digest)} ({type(exc).__name__}: {exc})")
    return report


def migrate_legacy(home: Path) -> dict:
    """One explicitly selected old target path, with no raw/cipher conflict overwrites."""
    from tools import browser_terminal

    env = browser_terminal.terminal_environment(create=True)
    runtime = browser_terminal.browser_runtime(env)
    lease = profiles._new_lease("explicit-legacy", {}, env, runtime, "")
    report = {"saved": [], "retained": []}
    path = profiles._legacy_path(lease)
    lease.legacy_path = path
    with state.locked(home, lease.digest, lease.base):
        if state.read(home, lease.digest, lease.custody) is not None:
            raise state.OutgoingProfileError("Pending outgoing recovery must be settled before legacy conversion")
        if archive.archive_ciphertext_size(home, lease.relative) is not None:
            raise state.OutgoingProfileError("Both old raw profile and current archive may exist; neither was overwritten")
        state.write(home, lease.digest, lease.custody,
                    profiles.document(lease, "active", legacy_path=path))
        profiles._checkpoint(lease, path=path, stale=True)
        report["saved"].append(lease.relative.as_posix())
    return report
