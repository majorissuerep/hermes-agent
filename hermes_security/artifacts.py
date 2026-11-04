"""Profile-owned private artifacts: encrypted bytes, never remote upload staging.

Only these reserved output roots receive artifact reads. This is not a general
decrypting file API for config, credentials, or another profile's state.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import stat

from hermes_security import frames
from hermes_security.artifact_paths import ARTIFACT_ROOTS, is_artifact_relative_path, terminal_frame_purpose
from hermes_security.errors import VaultLockedError

_ROOTS = ARTIFACT_ROOTS
_MAX_BYTES = 64 * 1024 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _binding(path, home=None):
    if home is None:
        from hermes_constants import get_hermes_home

        home = get_hermes_home()
    configured = Path(os.path.abspath(Path(home).expanduser()))
    selected = configured.resolve()
    candidate = Path(os.path.abspath(Path(path).expanduser()))
    if candidate.is_relative_to(configured):
        candidate = selected / candidate.relative_to(configured)
    return selected, candidate


def is_artifact_path(path, *, home=None) -> bool:
    """Recognize explicit reserved roots, including foreign ones to refuse safely."""
    selected, candidate = _binding(path, home)
    if any(candidate == selected.joinpath(*root) or selected.joinpath(*root) in candidate.parents
           for root in _ROOTS):
        return True
    # A foreign vaulted home cannot become ordinary host/remote file access.
    return any((parent / ".hermes-vault").is_file()
               and is_artifact_relative_path(candidate.relative_to(parent)) for parent in candidate.parents)


def _location(path, home=None):
    selected, candidate = _binding(path, home)
    try:
        relative = candidate.relative_to(selected)
    except ValueError as exc:
        raise PermissionError("Private artifact belongs to a different profile") from exc
    if not is_artifact_relative_path(relative):
        raise PermissionError("Not an admitted private artifact path")
    if not (selected / ".hermes-vault").is_file():
        raise VaultLockedError("Private output requires an initialized, unlocked profile vault")
    return selected, relative


class _Custody:
    """Keep the original unlock and adopted lineage, never a later ambient unlock."""
    def __init__(self, home):
        from hermes_security.session_authority import capture_inherited_authority
        from hermes_security.vault import get_vault

        self.vault = get_vault(home)
        self.authority = capture_inherited_authority(home) if self.vault._borrowed else None
        self.revoked = False
        self.check()

    def check(self):
        from hermes_security.session_authority import SessionAuthorityError

        if self.revoked:
            raise VaultLockedError("Private artifact's original custody was revoked")
        try:
            self.vault.assert_custody_live()
            if self.authority is not None:
                self.authority.validate()
        except (VaultLockedError, SessionAuthorityError) as exc:
            self.revoked = True
            raise VaultLockedError("Private artifact's original custody was revoked") from exc

    def derive_key(self, purpose):
        self.check()
        return self.vault.derive_key_for_custody(purpose, authority=self.authority)


@contextmanager
def _parent(home: Path, relative: Path, *, create=False):
    """Pin each directory inode below the owning home; never follow a planted link."""
    fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW)
    try:
        for part in relative.parts[:-1]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if info.st_uid != os.getuid():
                raise PermissionError("Private artifact directory has a different owner")
            if stat.S_IMODE(info.st_mode) != 0o700:
                if not create:
                    raise PermissionError("Private artifact directory must be owner-only")
                os.fchmod(fd, 0o700)
        yield fd
    finally:
        os.close(fd)


def _write_all(fd, data):
    pending = memoryview(data)
    while pending:
        written = os.write(fd, pending)
        if not written:
            raise OSError("Incomplete private artifact write")
        pending = pending[written:]


def _stream(relative):
    return terminal_frame_purpose(relative) is not None


def _wire_bound():
    # The limit governs plaintext. Reserve envelope overhead and the maximum
    # frame overhead from the sink's >=4096-byte batches plus its final batch.
    batches = (_MAX_BYTES + 4095) // 4096 + 1
    return _MAX_BYTES + max(128, batches * frames._FRAME_OVERHEAD)


def _bound_custody(home, custody=None):
    metadata = home / ".hermes-vault"
    if not metadata.is_file() or metadata.is_symlink():
        raise VaultLockedError("Private output requires this exact initialized profile")
    custody = _Custody(home) if custody is None else custody
    if custody.vault.home != home:
        raise PermissionError("Private artifact custody belongs to a different profile")
    custody.check()
    return custody


def write_bytes(path, data: bytes, *, home=None, overwrite=False, purpose="state", custody=None) -> Path:
    """Atomically publish ciphertext; binary browser artifacts use purpose ``state``."""
    if purpose != "state" or len(data) > _MAX_BYTES:
        raise ValueError("Unsupported private artifact purpose or size")
    selected, relative = _location(path, home)
    custody = _bound_custody(selected, custody)
    encoded = (frames.encode_frames([data], vault=custody, purpose=terminal_frame_purpose(relative)) if _stream(relative)
               else custody.vault.encrypt(data, purpose="state", relpath=relative.as_posix(),
                                          authority=custody.authority))
    return _publish(selected, relative, encoded, custody, overwrite=overwrite)


def _publish(selected, relative, encoded, custody, *, overwrite=False):
    """Publish already sealed bytes beneath a validated owning home and relative path."""
    with _parent(selected, relative, create=True) as parent:
        name = relative.name
        temporary = ".artifact-" + secrets.token_hex(16)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600, dir_fd=parent)
        try:
            try:
                _write_all(fd, encoded)
                os.fsync(fd)
            finally:
                os.close(fd)
            custody.check()
            if overwrite:
                try:
                    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                        raise PermissionError("Private artifact replacement requires an owned regular file")
                except FileNotFoundError:
                    pass
                os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
            else:
                os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            os.fsync(parent)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
    return selected / relative


def _read_owned_bytes(selected, relative, wire_bound):
    with _parent(selected, relative) as parent:
        fd = os.open(relative.name, os.O_RDONLY | _NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise PermissionError("Private artifact requires an owner-only regular file")
            if info.st_size > wire_bound:
                raise ValueError("Private artifact exceeds the supported ciphertext read bound")
            raw = source.read(wire_bound + 1)
    if len(raw) > wire_bound:
        raise ValueError("Private artifact exceeds the supported read bound")
    return raw


def read_bytes(path, *, home=None, purpose="state", custody=None) -> bytes:
    """Authenticate an exact own-profile artifact, refusing incomplete or oversized files."""
    if purpose != "state":
        raise ValueError("Unsupported private artifact purpose")
    selected, relative = _location(path, home)
    custody = _bound_custody(selected, custody)
    wire_bound = _wire_bound()
    raw = _read_owned_bytes(selected, relative, wire_bound)
    custody.check()
    if not _stream(relative):
        decoded = custody.vault.decrypt(raw, purpose="state", relpath=relative.as_posix(), authority=custody.authority)
    else:
        chunks, consumed = frames.split_stream(raw, vault=custody, purpose=terminal_frame_purpose(relative), strict=True)
        if consumed != len(raw):
            raise ValueError("Private terminal artifact is incomplete")
        decoded = b"".join(chunks)
    if len(decoded) > _MAX_BYTES:
        raise ValueError("Private artifact exceeds the supported plaintext read bound")
    return decoded


def read_text(path, *, home=None) -> str:
    return read_bytes(path, home=home).decode("utf-8", errors="replace")


class _TextSink:
    def __init__(self, path, home=None):
        self.home, self.relative = _location(path, home)
        if not _stream(self.relative):
            raise ValueError("A private text sink requires the terminal-output root")
        self.custody = _bound_custody(self.home)
        self.pending = bytearray()
        self.total_bytes = 0
        with _parent(self.home, self.relative, create=True) as parent:
            self.fd = os.open(self.relative.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
                              0o600, dir_fd=parent)

    def write(self, text):
        self.custody.check()
        payload = text.encode("utf-8", errors="replace")
        if self.total_bytes + len(payload) > _MAX_BYTES // 2:
            raise ValueError("Private output stream exceeds its supported bound")
        self.pending.extend(payload)
        self.total_bytes += len(payload)
        # Coalesce tiny stdout reads in bounded RAM so frame overhead cannot
        # make the collector's 5M-character archive exceed the read bound.
        if len(self.pending) >= 4096:
            self._flush()
        return len(text)

    def _flush(self):
        if self.pending:
            encoded = frames.encode_frames([bytes(self.pending)], vault=self.custody,
                                           purpose=terminal_frame_purpose(self.relative))
            _write_all(self.fd, encoded)
            self.pending.clear()

    def close(self):
        if self.fd >= 0:
            try:
                self._flush()
                os.fsync(self.fd)
            finally:
                fd, self.fd = self.fd, -1
                self.pending.clear()
                os.close(fd)


def open_text(path, *, home=None):
    """Exclusive descriptor-backed encrypted stream; each chunk is sealed before write."""
    return _TextSink(path, home)
