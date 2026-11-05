"""Host-owned encrypted recovery references and locking for outgoing browser profiles.

References contain no keys/capabilities. Browser secrets remain in the dedicated
profile archive; these small state envelopes fence pending RAM and full restore.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat

from hermes_security import artifacts, browser_profile_archive as archive, tree_archive

ROOT = Path("state/browser-outgoing")
_BOUND = 32768


class OutgoingProfileError(RuntimeError):
    """Continuity could not be established; existing ciphertext and target state are retained."""


def logical_identity(custody, backend: str, env, facts, *, daemon: str | None = None) -> dict:
    """Stable selected target/account, distinct from its immutable live generation.

    Configuration may contain private endpoint details: only a keyed digest is stored.
    SSH's existing authenticated transport owns host-key policy; the native machine
    identity additionally prevents a changed server from reusing another server's profile.
    """
    if backend == "ssh" and not facts.machine:
        raise OutgoingProfileError("The selected SSH target has no stable native machine identity; profile retained")
    if backend == "docker" and not daemon:
        raise OutgoingProfileError("The selected Docker daemon has no stable native identity; profile retained")
    return {"backend": backend, "selection": selection_digest(custody, backend, env),
            "uid": facts.uid, "home": facts.home, "machine": facts.machine, "daemon": daemon}


def selection_digest(custody, backend: str, env) -> str:
    if backend == "ssh":
        selected = [str(env.host), str(env.user), int(env.port)]
    elif backend == "docker":
        selected = [str(env._image), os.environ.get("DOCKER_HOST", ""), os.environ.get("DOCKER_CONTEXT", "")]
    elif backend == "singularity":
        selected = [str(env.image)]
    else:
        raise OutgoingProfileError("This backend cannot own an outgoing browser profile")
    document = tree_archive.canonical_json({"backend": backend, "selection": selected})
    key = custody.derive_key("browser-outgoing:v1:logical-target")
    return hmac.new(key, document, hashlib.sha256).hexdigest()


def relative(digest: str) -> Path:
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("Invalid outgoing browser identity")
    return ROOT / (digest + ".json")


@contextlib.contextmanager
def locked(home: Path, digest: str, base: Path):
    """The lock inode is retained; no cleanup unlinks a live lock."""
    import fcntl

    name = "hermes-outgoing-profile-" + hashlib.sha256(str(home).encode()).hexdigest()[:16] + "-" + digest[:32] + ".lock"
    parent = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                    or info.st_mode & 0o077):
                raise OutgoingProfileError("Outgoing browser continuity lock is unsafe")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise OutgoingProfileError("Outgoing browser profile is busy; existing copies retained") from exc
            yield
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def read(home: Path, digest: str, custody) -> dict | None:
    path = relative(digest)
    artifacts._bound_custody(home, custody)
    try:
        raw = artifacts._read_owned_bytes(home, path, _BOUND)
    except FileNotFoundError:
        return None
    decoded = custody.vault.decrypt(raw, purpose="state", relpath=path.as_posix(), authority=custody.authority)
    custody.check()
    document = json.loads(decoded)
    if (not isinstance(document, dict) or document.get("version") != 1
            or document.get("home") != str(home) or document.get("digest") != digest
            or document.get("phase") not in {"active", "restoring", "checkpointed"}):
        raise OutgoingProfileError("Outgoing browser recovery reference is invalid; retained")
    _validate(document)
    return document


def _validate(document):
    from tools.browser_recording_pending import _valid_owner
    from hermes_platform.remote_identity import RuntimeIdentity, guarded_argv

    backend, identity, runtime = (document.get(k) for k in ("backend", "identity", "runtime"))
    token = document.get("lease")
    if (backend not in {"ssh", "docker", "singularity"} or not isinstance(identity, dict)
            or identity.get("backend") != backend or not isinstance(runtime, dict)
            or not isinstance(document.get("origin"), dict) or not _valid_owner(document.get("owner"))
            or not isinstance(token, str) or len(token) != 32
            or any(c not in "0123456789abcdef" for c in token)):
        raise OutgoingProfileError("Outgoing browser owner/target metadata is damaged; retained")
    _validate_identity(identity)
    try:
        directories = tuple(tuple(pair) for pair in runtime["directories"])
        if (len(directories) != 5 or any(len(pair) != 2 for pair in directories)
                or any(not isinstance(p, str) or not p.startswith("/") or "\x00" in p for p, _ in directories)
                or any(not isinstance(stamp, str) or len(stamp.split(":")) != 4
                       or not all(part.isdecimal() for part in stamp.split(":")) for _, stamp in directories)
                or not isinstance(runtime["epoch"], str) or len(runtime["epoch"]) > 256):
            raise ValueError("invalid runtime")
        guarded_argv(RuntimeIdentity(runtime["epoch"], directories), ["true"])
    except (KeyError, TypeError, ValueError) as exc:
        raise OutgoingProfileError("Outgoing browser generation metadata is damaged; retained") from exc
    expected = archive.archive_relative(archive.OUTGOING_SLOT_PREFIX + backend, document["digest"]).as_posix()
    if document.get("archive") != expected:
        raise OutgoingProfileError("Outgoing browser archive identity changed; retained")
    if document["phase"] == "checkpointed":
        raw_hash, maximum = document.get("raw_hash"), document.get("maximum")
        if (not isinstance(document.get("recovery"), dict) or not isinstance(raw_hash, str)
                or len(raw_hash) != 64 or any(c not in "0123456789abcdef" for c in raw_hash)
                or type(maximum) is not int or not 0 < maximum <= 4 * 1024**3):
            raise OutgoingProfileError("Outgoing browser checkpoint metadata is damaged; retained")
        cipher = document.get("archive_cipher_sha256")
        if cipher is not None and (not isinstance(cipher, str) or len(cipher) != 64
                                   or any(c not in "0123456789abcdef" for c in cipher)):
            raise OutgoingProfileError("Outgoing browser checkpoint ciphertext identity is damaged; retained")


def _validate_identity(identity):
    def digest(value):
        return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    uid, home = identity.get("uid"), identity.get("home")
    if (not digest(identity.get("selection")) or type(uid) is not int or uid < 0
            or not isinstance(home, str) or not home.startswith("/") or len(home) > 4096 or "\x00" in home
            or any(identity.get(k) is not None and not digest(identity[k]) for k in ("machine", "daemon"))
            or (identity["backend"] == "docker" and not digest(identity.get("daemon")))
            or (identity["backend"] == "ssh" and not digest(identity.get("machine")))):
        raise OutgoingProfileError("Outgoing browser stable target identity is damaged; retained")


def write(home: Path, digest: str, custody, document: dict) -> None:
    artifacts._bound_custody(home, custody)
    path = relative(digest)
    data = tree_archive.canonical_json({**document, "version": 1, "home": str(home), "digest": digest})
    if len(data) > _BOUND // 2:
        raise OutgoingProfileError("Outgoing browser recovery reference exceeds its bound")
    custody.check()
    encoded = custody.vault.encrypt(data, purpose="state", relpath=path.as_posix(), authority=custody.authority)
    artifacts._publish(home, path, encoded, custody, overwrite=True)


def remove(home: Path, digest: str, custody, token: str, *, expected=None) -> None:
    artifacts._bound_custody(home, custody)
    path = relative(digest)
    with artifacts._parent(home, path) as parent:
        try:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError:
            return
        with os.fdopen(descriptor, "rb") as source:
            before = os.fstat(source.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                    or before.st_nlink != 1 or before.st_mode & 0o077):
                raise OutgoingProfileError("Outgoing recovery reference changed; retained")
            raw = source.read(_BOUND + 1)
            if len(raw) > _BOUND:
                raise OutgoingProfileError("Outgoing recovery reference exceeds its bound; retained")
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                    before.st_ctime_ns, hashlib.sha256(raw).hexdigest())
        if expected is not None and identity != expected:
            raise OutgoingProfileError("Outgoing recovery reference changed since verification; retained")
        document = json.loads(custody.vault.decrypt(raw, purpose="state", relpath=path.as_posix(), authority=custody.authority))
        if not secrets.compare_digest(str(document.get("lease")), token):
            raise OutgoingProfileError("A newer outgoing browser lease replaced this recovery reference; retained")
        custody.check()
        current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns) != (
                before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns):
            raise OutgoingProfileError("Outgoing recovery reference was replaced during cleanup; retained")
        os.unlink(path.name, dir_fd=parent)
        os.fsync(parent)


def pending(home: Path) -> bool:
    """Any pending or unrecognized entry fences restore without connecting to any backend."""
    try:
        with artifacts._parent(home, ROOT / ".inspection") as parent:
            return bool(_references(parent))
    except FileNotFoundError:
        return False


def _references(parent):
    names = []
    for name in os.listdir(parent):
        stem, suffix = os.path.splitext(name)
        try:
            relative(stem)
        except ValueError as exc:
            raise OutgoingProfileError("Unrecognized outgoing browser recovery state; retained") from exc
        if suffix == ".lock":
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_size or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or info.st_mode & 0o077):
                raise OutgoingProfileError("Outgoing browser lock metadata is damaged; retained")
        elif suffix == ".json":
            names.append(stem)
        else:
            raise OutgoingProfileError("Unrecognized outgoing browser recovery state; retained")
    return names


def references(home: Path, custody):
    try:
        with artifacts._parent(home, ROOT / ".inspection") as parent:
            names = _references(parent)
    except FileNotFoundError:
        return []
    return [(digest, read(home, digest, custody)) for digest in names]
