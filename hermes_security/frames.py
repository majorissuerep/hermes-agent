"""Encrypted append-only frame streams for logs and transcripts.

Each record is serialized to bytes and sealed into its own AES-GCM frame:
``u32 BE length | nonce | ciphertext``.  A crash-truncated tail frame is
ignored on read; every earlier frame remains independently verifiable.
Appends are serialized across processes with an flock'd sidecar lock.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import struct
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Iterator, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from hermes_security.errors import VaultError, VaultIntegrityError
from hermes_security.vault import find_vault_home, get_vault

_LENGTH = struct.Struct(">I")
_NONCE_BYTES = 12
_FRAME_OVERHEAD = _LENGTH.size + _NONCE_BYTES + 16
_ENVELOPE_MAGIC = b"HRMVAULT\x00"

_IS_WINDOWS = sys.platform == "win32"
# Cache authenticated CONTENT, never stat timestamps (same-tick overwrites are
# possible). Every append hashes the bytes under the lock; changed bytes require
# authentication again. This avoids per-frame crypto on the common single-writer
# path, but still costs a sequential read proportional to log size.
_VALIDATED_STREAMS: OrderedDict = OrderedDict()


def _complete_stream(blob: bytes, *, vault, purpose: str) -> list[bytes]:
    payloads, consumed = split_stream(blob, vault=vault, purpose=purpose, strict=True)
    if consumed != len(blob):
        raise VaultIntegrityError(
            "Incomplete trailing frame; refusing to change the stream. "
            "Recover it with secure-vault repair before retrying.")
    return payloads


def _flock(fd: int) -> None:
    """Cross-process append serialization (advisory)."""

    if _IS_WINDOWS:
        import msvcrt

        os.lseek(fd, 0, os.SEEK_END)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_EX)


def _funlock(fd: int) -> None:
    if _IS_WINDOWS:
        import msvcrt

        os.lseek(fd, 0, os.SEEK_END)
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        except OSError:
            pass
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


class FrameDecodeError(VaultError):
    """A frame stream is unreadable beyond the truncated tail."""


def _frame_key(vault, purpose: str) -> bytes:
    return vault.derive_key("frames:" + purpose)


def append(path: Path | str, payload: bytes, *, purpose: str) -> None:
    """Append one encrypted frame to *path* under an inter-process lock."""

    vault = get_vault(find_vault_home(path))
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    aad = f"hermes-frames|1|{purpose}".encode("utf-8")
    ciphertext = AESGCM(_frame_key(vault, purpose)).encrypt(nonce, payload, aad)
    frame = _LENGTH.pack(len(nonce) + len(ciphertext)) + nonce + ciphertext

    lock_path = target.with_suffix(target.suffix + ".lock")
    with open(lock_path, "ab") as lock_file:
        _flock(lock_file.fileno())
        try:
            existed = target.exists()
            with open(target, "a+b") as stream:
                cache_key = (str(target.resolve()), purpose, _frame_key(vault, purpose))
                stream.seek(0)
                blob = stream.read()
                digest = hashlib.sha256(blob)
                if _VALIDATED_STREAMS.pop(cache_key, None) != digest.digest():
                    _complete_stream(blob, vault=vault, purpose=purpose)
                # Invalidate before writing: an exception must never cache a partial append.
                stream.write(frame)
                stream.flush()
                os.fsync(stream.fileno())
                digest.update(frame)
                _VALIDATED_STREAMS[cache_key] = digest.digest()
                while len(_VALIDATED_STREAMS) > 128:
                    _VALIDATED_STREAMS.popitem(last=False)
            if not existed:
                os.chmod(target, 0o600)
        finally:
            _funlock(lock_file.fileno())


def read_frames(path: Path | str, *, purpose: str) -> Iterator[bytes]:
    """Yield authentic frames; tolerate only an incomplete tail, never a failed tag."""

    vault = get_vault(find_vault_home(path))
    target = Path(path).expanduser()
    try:
        blob = target.read_bytes()
    except FileNotFoundError:
        return
    if blob.startswith(_ENVELOPE_MAGIC):
        # A whole-file envelope is not a frame stream; parsing its magic as a
        # length prefix reads as a "truncated tail" and silently yields nothing.
        raise VaultIntegrityError(f"{target} is an envelope, not a frame stream")
    key = _frame_key(vault, purpose)
    aad = f"hermes-frames|1|{purpose}".encode("utf-8")
    offset = 0
    total = len(blob)
    while offset < total:
        if offset + _LENGTH.size > total:
            break  # truncated tail length prefix
        (size,) = _LENGTH.unpack_from(blob, offset)
        if size < _NONCE_BYTES + 16:
            raise VaultIntegrityError(f"Invalid frame length at offset {offset}")
        frame_start = offset + _LENGTH.size
        frame_end = frame_start + size
        if frame_end > total:
            break  # truncated tail frame - ignore, prefix stays verifiable
        nonce = blob[frame_start : frame_start + _NONCE_BYTES]
        ciphertext = blob[frame_start + _NONCE_BYTES : frame_end]
        try:
            yield AESGCM(key).decrypt(nonce, ciphertext, aad)
        except InvalidTag as exc:
            raise VaultIntegrityError(
                f"Frame stream {target} is corrupt at offset {offset}"
            ) from exc
        offset = frame_end


def decode_frame_at(blob: bytes, offset: int, *, vault, purpose: str,
                    strict: bool = False) -> Optional[tuple[bytes, int]]:
    """Return an authenticated frame. Strict consumers distinguish corruption from truncation;
    the non-strict mode is reserved for repair's byte-by-byte salvage probes."""

    if offset + _LENGTH.size > len(blob):
        return None
    (size,) = _LENGTH.unpack_from(blob, offset)
    start = offset + _LENGTH.size
    end = start + size
    if size < _NONCE_BYTES + 16:
        if strict:
            raise VaultIntegrityError(f"Invalid frame length at offset {offset}")
        return None
    if end > len(blob):
        return None
    aad = f"hermes-frames|1|{purpose}".encode("utf-8")
    try:
        payload = AESGCM(_frame_key(vault, purpose)).decrypt(
            blob[start : start + _NONCE_BYTES], blob[start + _NONCE_BYTES : end], aad)
    except InvalidTag as exc:
        if strict:
            raise VaultIntegrityError(f"Frame authentication failed at offset {offset}") from exc
        return None
    return payload, end


def split_stream(blob: bytes, *, vault, purpose: str, strict: bool = False) -> tuple[list[bytes], int]:
    """Decrypt a frame prefix. Strict consumers reject corruption, salvage probes may stop."""

    if strict and blob.startswith(_ENVELOPE_MAGIC):
        raise VaultIntegrityError("An envelope is not a frame stream")
    payloads: list[bytes] = []
    offset = 0
    while (frame := decode_frame_at(blob, offset, vault=vault, purpose=purpose, strict=strict)) is not None:
        payloads.append(frame[0])
        offset = frame[1]
    return payloads, offset


def encode_frames(payloads: list[bytes], *, vault, purpose: str) -> bytes:
    """Serialize *payloads* as a complete frame stream (same format as ``append``)."""

    key = _frame_key(vault, purpose)
    aad = f"hermes-frames|1|{purpose}".encode("utf-8")
    out = bytearray()
    for payload in payloads:
        nonce = secrets.token_bytes(_NONCE_BYTES)
        ciphertext = AESGCM(key).encrypt(nonce, payload, aad)
        out += _LENGTH.pack(len(nonce) + len(ciphertext)) + nonce + ciphertext
    return bytes(out)


def rewrite(path: Path | str, transform: Callable[[list[bytes]], list[bytes]], *, purpose: str) -> None:
    """Replace *path* with ``transform(current payloads)`` atomically, under the same lock
    ``append`` takes, so no concurrent append is lost. Compaction and true erasure: payloads
    the transform drops leave no ciphertext behind in the stream."""

    from hermes_security.vault import _atomic_write

    vault = get_vault(find_vault_home(path))
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = target.with_suffix(target.suffix + ".lock")
    with open(lock_path, "ab") as lock_file:
        _flock(lock_file.fileno())
        try:
            try:
                blob = target.read_bytes()
            except FileNotFoundError:
                blob = b""
            payloads = _complete_stream(blob, vault=vault, purpose=purpose)
            _atomic_write(target, encode_frames(transform(payloads), vault=vault, purpose=purpose))
        finally:
            _funlock(lock_file.fileno())


def rotate(path: Path | str) -> Optional[Path]:
    """Rotate *path* to ``<name>.1`` (single generation), best effort."""

    target = Path(path).expanduser()
    if not target.exists():
        return None
    rotated = target.with_suffix(target.suffix + ".1")
    os.replace(target, rotated)
    return rotated
