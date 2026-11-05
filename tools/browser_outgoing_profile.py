"""Durable continuity for one host-owned browser profile in a selected outgoing target.

Only a confirmed normal close is checkpointed automatically. A failure retains its
lease, encrypted recovery reference and exact target RAM; new requests never adopt it.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import copy_context
from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading

from hermes_constants import get_hermes_home
from hermes_security import artifacts, browser_profile_archive as archive
from hermes_security import protected_workspace as ram_workspaces
from tools import browser_outgoing_state as state, browser_outgoing_transfer as transfer
from tools import browser_outgoing_retirement as retirement
from tools import browser_outgoing_inventory as native_inventory

_LOCK = threading.RLock()
_LEASES = {}


def environment_lock(env):
    """Serialize private commands with this exact environment's destructive boundary only."""
    with _LOCK:
        lock = vars(env).get("_hermes_browser_profile_lock")
        if lock is None:
            lock = env._hermes_browser_profile_lock = threading.RLock()
        return lock


@contextmanager
def target_scope(env):
    with environment_lock(env):
        if vars(env).get("_hermes_browser_profile_retired", False):
            raise state.OutgoingProfileError("This outgoing environment was retired; its old browser was not reattached")
        yield


def retire_environment(env):
    """Called while the exact environment lock is held, after preservation or explicit force."""
    env._hermes_browser_profile_retired = True


def ended_resident(home, digest, reference):
    """Only our exact resident task can prove ended local custody while its process lives."""
    from hermes_security.errors import VaultLockedError
    if reference["owner"] != _owner():
        raise state.OutgoingProfileError("Original browser owner is live or unknown in another process; retained")
    with _LOCK:
        candidates = [lease for lease in _LEASES.values()
                      if lease.home == home and lease.digest == digest and lease.token == reference["lease"]]
    if len(candidates) != 1:
        raise state.OutgoingProfileError("Original owner is live or unknown; no exact resident ended task can be proven; RAM retained")
    lease = candidates[0]
    lease.custody.resource.check()  # Vault loss is not evidence that the task's source ended.
    try:
        lease.custody.check()
    except VaultLockedError:
        return lease  # original SessionCustody is permanently refused; never revived or reused
    raise state.OutgoingProfileError("Original outgoing task source remains live; its RAM was retained")


def forget_settled(lease):
    """Metadata only, after a NEW request saved and retired this exact ended task's RAM."""
    from tools.browser_tool_lifecycle import _forget_session_tracking, _drop_last_active_binding
    with _LOCK:
        key = lease.home, lease.task_id
        if _LEASES.get(key) is lease:
            del _LEASES[key]
    if _forget_session_tracking(lease.task_id, session=True, expected_session=lease.session_info):
        _drop_last_active_binding(lease.task_id)


def exclusive_resident(lease):
    """No other tracked task may share the exact session or profile being explicitly closed."""
    from tools import browser_tool
    name = lease.session_info.get("session_name")
    if not isinstance(name, str) or not name or len(name) > 256:
        raise state.OutgoingProfileError("Original browser session identity is unknown; retained")
    with browser_tool._cleanup_lock:
        if (browser_tool._active_sessions.get(lease.task_id) is not lease.session_info
                or any(task != lease.task_id and info.get("session_name") == name
                       for task, info in browser_tool._active_sessions.items())):
            raise state.OutgoingProfileError("Original browser session is shared or was replaced; retained")
    with _LOCK:
        if any(other is not lease and other.home == lease.home and other.digest == lease.digest
               for other in _LEASES.values()):
            raise state.OutgoingProfileError("Another task retains this browser profile; close refused")
    return name


@dataclass
class Lease:
    home: Path
    backend: str
    env: object
    runtime: object
    custody: object
    context: object
    client_env: dict
    task_id: str
    session_info: dict
    browser_cmd: str
    engine: str
    identity: dict = field(default_factory=dict)
    digest: str = ""
    token: str = field(default_factory=lambda: secrets.token_hex(16))
    closed: bool = False
    capacity_source_sha256: str = ""
    base: Path | None = None
    base_identity: tuple[int, int] = ()
    bound: bool = False
    legacy_path: str | None = None

    @property
    def slot(self):
        return archive.OUTGOING_SLOT_PREFIX + self.backend

    @property
    def relative(self):
        return archive.archive_relative(self.slot, self.digest)

    @property
    def binding(self):
        return {"version": 1, "home": str(self.home), "identity": self.digest, "lease": self.token}


@contextmanager
def workspace(lease, purpose):
    lease.custody.check()
    info = os.stat(lease.base, follow_symlinks=False)
    if (info.st_dev, info.st_ino) != lease.base_identity or Path(os.path.realpath(lease.base)) != lease.base:
        raise state.OutgoingProfileError("The original host protected RAM base changed; target and archive retained")
    with ram_workspaces.protected_workspace(purpose, base=lease.base) as ram:
        yield ram


def _owner():
    from tools.browser_recording_pending import _owner_identity
    return _owner_identity()


def document(lease, phase: str, **extra):
    if lease.runtime.generation is None:
        raise state.OutgoingProfileError("The outgoing browser has no immutable native generation")
    origin = {"container": getattr(lease.env, "_container_id", None),
              "instance": getattr(lease.env, "instance_id", None)}
    return {"phase": phase, "lease": lease.token, "owner": _owner(), "identity": lease.identity, "origin": origin,
            "backend": lease.backend, "runtime": asdict(lease.runtime.generation),
            "archive": lease.relative.as_posix(), "legacy_path": lease.legacy_path, **extra}


def _key(task_id: str):
    return Path(get_hermes_home()).resolve(), task_id


def lookup(task_id: str):
    with _LOCK:
        selected = _LEASES.get(_key(task_id))
        if selected is None and any(lease.task_id == task_id for lease in _LEASES.values()):
            raise state.OutgoingProfileError("This browser belongs to a different original profile; no target was used")
        return selected


def _require_session(lease, info=None):
    from tools import browser_tool
    with browser_tool._cleanup_lock:
        current = browser_tool._active_sessions.get(lease.task_id)
    if current is not lease.session_info or (info is not None and info is not lease.session_info):
        raise state.OutgoingProfileError("The original outgoing browser session was replaced; recovery state retained")
    artifacts._bound_custody(lease.home, lease.custody)


@contextmanager
def request_scope(lease):
    """A caller may not borrow the live lease's source after its own source was revoked."""
    from hermes_security.session_custody import SessionCustody
    if Path(get_hermes_home()).resolve() != lease.home:
        raise state.OutgoingProfileError("Outgoing browser request belongs to a different selected home")
    operation = SessionCustody.capture(lease.home, resource=lease.custody.resource)
    operation.check()
    yield
    operation.check()


@contextmanager
def command_scope(task_id: str):
    from tools.browser_terminal import captured_execution
    lease = lookup(task_id)
    if lease is None:
        yield
        return
    with request_scope(lease), target_scope(lease.env):
        _require_session(lease)
        with captured_execution(lease.home, lease.backend, lease.env, lease.runtime):
            yield


def _new_lease(task_id, info, env, runtime, browser_cmd):
    from tools import browser_terminal
    from tools.browser_recording import _CLIENT_ENV
    from tools.browser_tool_session import _cloud
    from hermes_security.session_custody import SessionCustody

    home = Path(get_hermes_home()).resolve()
    owner = vars(env).setdefault("_hermes_browser_owner_home", home)
    if Path(owner).resolve() != home:
        raise state.OutgoingProfileError("This outgoing environment belongs to another original Hermes home")
    env._hermes_browser_backend = browser_terminal.terminal_backend()
    custody = SessionCustody.capture(home, resource=artifacts._bound_custody(home))
    from hermes_security.operation_custody import guarded
    custody = guarded(custody)
    lease = Lease(home, browser_terminal.terminal_backend(), env, runtime, custody, copy_context(),
                  {name: os.environ[name] for name in _CLIENT_ENV if name in os.environ},
                  task_id, info, browser_cmd, _cloud._get_browser_engine())
    lease.base = Path(os.path.realpath(ram_workspaces.protected_workspace_base()))
    status = ram_workspaces.protected_workspace_status(lease.base)
    if not status.proven:
        raise state.OutgoingProfileError(f"Outgoing browser continuity requires protected host RAM ({status.reason})")
    base_info = os.stat(lease.base, follow_symlinks=False)
    lease.base_identity = base_info.st_dev, base_info.st_ino
    facts = transfer.capacity(lease)
    daemon = transfer.docker_identity(lease) if lease.backend == "docker" else None
    lease.identity = state.logical_identity(custody, lease.backend, env, facts, daemon=daemon)
    lease.digest = archive.identity_digest(custody, lease.slot, lease.identity)
    return lease


def _legacy_path(lease):
    import posixpath
    return posixpath.join(lease.identity["home"], ".cache/hermes-agent/browser",
                          hashlib.sha256(str(lease.home).encode()).hexdigest()[:16])


def _legacy_absent(lease):
    from hermes_platform.remote_profile import existence_command
    present = json.loads(transfer.client(lease, existence_command(_legacy_path(lease))))
    if present is not False:
        raise state.OutgoingProfileError(
            f"The selected target retains an older browser profile at {_legacy_path(lease)}. "
            "No data was remapped. Close that browser and run "
            "'hermes secure-vault repair --outgoing-browser-legacy' explicitly")


def prepare(task_id: str, info: dict, env, runtime, browser_cmd: str):
    """Admit/restore before the FIRST private command; subsequent commands retain original custody."""
    if info.get("cdp_url"):
        return None  # explicitly selected external CDP owns its browser, not this profile
    existing = lookup(task_id)
    if existing is not None:
        _require_session(existing, info)
        if existing.closed:
            raise state.OutgoingProfileError("This outgoing browser is closed with pending recovery; run repair")
        return existing
    lease = _new_lease(task_id, info, env, runtime, browser_cmd)
    with state.locked(lease.home, lease.digest, lease.base):
        if state.read(lease.home, lease.digest, lease.custody) is not None:
            raise state.OutgoingProfileError(
                "An outgoing browser profile still has pending RAM/recovery state. Close its owning Hermes "
                "and run 'hermes secure-vault repair --outgoing-browser-profile'; it was not adopted")
        _legacy_absent(lease)
        if not transfer.inspect(lease):
            raise state.OutgoingProfileError("Unclaimed outgoing browser RAM was retained; explicit repair is required")
        if archive.archive_ciphertext_size(lease.home, lease.relative) is not None:
            _restore(lease)
        else:
            state.write(lease.home, lease.digest, lease.custody, document(lease, "restoring", recovery=None))
            transfer.claim(lease)
        state.write(lease.home, lease.digest, lease.custody, document(lease, "active"))
        with _LOCK:
            _LEASES[(lease.home, task_id)] = lease
    return lease


def _restore(lease):
    # Authentication/capacity precede marker publication and every target write.
    with workspace(lease, "outgoing-browser-restore") as ram:
        summary, spool, restored = transfer.prepare_restore(lease, ram)
        state.write(lease.home, lease.digest, lease.custody,
                    document(lease, "restoring", recovery=asdict(summary)))
        transfer.claim(lease)
        transfer.restore_prepared(lease, ram, summary, spool, restored)


def _checkpoint(lease, *, stale=False, path=None):
    previous = state.read(lease.home, lease.digest, lease.custody)
    if previous and previous.get("phase") == "checkpointed":
        _finish_checkpoint(lease, previous, stale=stale, path=path)
        return
    with workspace(lease, "outgoing-browser-save") as ram:
        original, _first_hash, _first_size = native_inventory.receive(
            lease, ram, "native-before.json", path=path, stale=stale)
        source, expected, raw_hash, maximum = transfer.download(lease, ram, path=path, stale=stale)
        current, native_hash, native_size = native_inventory.receive(
            lease, ram, "native-after.json", path=path, stale=stale)
        if current != original:
            raise state.OutgoingProfileError("Original native profile changed during checkpoint; every copy retained")
        native_bound = native_inventory.effect_limit(lease, native_size)
        written = archive.write_archive(lease.home, lease.relative, source, custody=lease.custody,
                                         slot=lease.slot, identity=lease.identity, digest=lease.digest)
        with retirement.pinned_archive(lease) as saved:
            checked = saved.replay(lease)
            if checked != written or checked != expected:
                raise state.OutgoingProfileError("Browser checkpoint verification failed; every copy retained")
            with native_inventory.published(lease, ram, original) as (metadata, proof):
                if state.read(lease.home, lease.digest, lease.custody) != previous:
                    raise state.OutgoingProfileError("Original checkpoint reference changed; every copy retained")
                state.write(lease.home, lease.digest, lease.custody,
                            document(lease, "checkpointed", recovery=asdict(checked), raw_hash=raw_hash,
                                     maximum=maximum, archive_cipher_sha256=saved.digest, inventory=metadata))
                reference = state.read(lease.home, lease.digest, lease.custody)
                retirement.finish(lease, reference, stale=stale, path=path, saved=saved, inventory=proof,
                                  original=(raw_hash, maximum, native_hash, native_bound))


def _finish_checkpoint(lease, document, *, stale=False, path=None):
    retirement.finish(lease, document, stale=stale, path=path)


def close(task_id: str, info: dict) -> bool | None:
    """None=native/external CDP; True=closed+saved; failure raises without forgetting target state."""
    lease = lookup(task_id)
    if lease is None:
        return None
    with request_scope(lease):
        return _close(lease, task_id, info)


def _close(lease, task_id, info):
    from tools import browser_tool_session as session
    from tools.browser_terminal import captured_execution
    _require_session(lease, info)
    with state.locked(lease.home, lease.digest, lease.base):
        def finish():
            with captured_execution(lease.home, lease.backend, lease.env, lease.runtime):
                require_saved_recordings(lease.env)
                if not transfer.inspect(lease, path=lease.runtime.captures):
                    raise state.OutgoingProfileError(
                        f"Unrelated captures remain at {lease.runtime.captures}; target and recovery reference retained")
                if not lease.closed:
                    _, result = session._dispatch_browser_command(task_id, info, lease.browser_cmd, "close", [], 10, lease.engine)
                    if not result.get("success"):
                        raise state.OutgoingProfileError("Original outgoing browser close failed; profile RAM retained")
                    lease.closed = True
                transfer.inspect(lease)
                _checkpoint(lease)
        lease.context.copy().run(finish)
        with _LOCK:
            if _LEASES.get((lease.home, task_id)) is lease:
                del _LEASES[(lease.home, task_id)]
    return True


def before_destroy(env) -> None:
    """Exact environment's leases only; failed preservation refuses ordinary backend destruction."""
    with _LOCK:
        leases = [lease for lease in _LEASES.values() if lease.env is env and _same_target(lease.runtime, env)]
    if leases:
        from tools.browser_tool_lifecycle import _cleanup_single_browser_session
        for lease in leases:
            with request_scope(lease):
                lease.context.copy().run(_cleanup_single_browser_session, lease.task_id)
    require_saved_recordings(env)
    _require_no_cold_pending(env)


def require_saved_recordings(env):
    from tools import browser_recording
    with browser_recording._LOCK:
        pending = [recording for recording in browser_recording._RECORDINGS.values()
                   if recording.env is env and _same_target(recording.runtime, env)]
    if pending:
        raise state.OutgoingProfileError(
            f"An outgoing browser recording remains at {pending[0].capture}; backend destruction was refused")


def _same_target(runtime, env) -> bool:
    from tools.environments.streams import exec_prefix
    return runtime is not None and tuple(exec_prefix(env, interactive=False) or ()) == runtime.client_identity


def retained_origins(env) -> str:
    with _LOCK:
        locations = [lease.runtime.profile for lease in _LEASES.values() if lease.env is env]
    return ", ".join(locations[:5]) or "the originally captured backend RAM locations"


def _backend(env):
    from tools.environments.docker import DockerEnvironment
    from tools.environments.ssh import SSHEnvironment
    from tools.environments.singularity import SingularityEnvironment
    for kind, name in ((DockerEnvironment, "docker"), (SSHEnvironment, "ssh"), (SingularityEnvironment, "singularity")):
        if isinstance(env, kind):
            return name
    return vars(env).get("_hermes_browser_backend")


def _require_no_cold_pending(env):
    backend = _backend(env)
    if backend is None:
        return
    owner = vars(env).get("_hermes_browser_owner_home")
    if owner is None:
        raise state.OutgoingProfileError("Outgoing environment owner home is unknown; ordinary teardown was refused")
    home = Path(owner)
    if not state.pending(home):
        return
    from hermes_security.session_custody import SessionCustody
    custody = SessionCustody.capture(home)
    for _digest, reference in state.references(home, custody):
        if reference is None or reference.get("backend") != backend:
            continue
        if _same_origin(reference, env, custody, backend):
            raise state.OutgoingProfileError(
                "This original backend retains encrypted recovery references from an earlier process; "
                "ordinary destruction was refused. Run 'hermes secure-vault repair --outgoing-browser-profile'")


def _same_origin(reference, env, custody, backend):
    origin = reference.get("origin")
    if not isinstance(origin, dict):
        raise state.OutgoingProfileError("Outgoing origin metadata is damaged; backend retained")
    if backend == "docker":
        return origin.get("container") == getattr(env, "_container_id", None)
    if backend == "singularity":
        return origin.get("instance") == getattr(env, "instance_id", None)
    identity = reference.get("identity")
    if not isinstance(identity, dict):
        raise state.OutgoingProfileError("Outgoing selection metadata is damaged; backend retained")
    return identity.get("selection") == state.selection_digest(custody, backend, env)
