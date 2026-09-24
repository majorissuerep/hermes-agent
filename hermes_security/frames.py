"""Encrypted append-only frame streams for logs and transcripts.

Each record is serialized to bytes and sealed into its own AES-GCM frame:
``u32 BE length | nonce | ciphertext``.  A crash-truncated tail frame is
ignored on read; every earlier frame remains independently verifiable.
Appends are serialized across processes with an flock'd sidecar lock.
"""

from __future__ import annotations

import os
import secrets
import struct
import sys
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
            with open(target, "ab") as stream:
                stream.write(frame)
                stream.flush()
                os.fsync(stream.fileno())
            if not existed:
                os.chmod(target, 0o600)
        finally:
            _funlock(lock_file.fileno())


def read_frames(path: Path | str, *, purpose: str) -> Iterator[bytes]:
    """Yield decrypted frames in order.  A truncated/corrupt TAIL frame stops
    iteration with its prefix intact; a corrupt NON-tail frame is an error."""

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
        frame_start = offset + _LENGTH.size
        frame_end = frame_start + size
        if frame_end > total:
            break  # truncated tail frame - ignore, prefix stays verifiable
        nonce = blob[frame_start : frame_start + _NONCE_BYTES]
        ciphertext = blob[frame_start + _NONCE_BYTES : frame_end]
        try:
            yield AESGCM(key).decrypt(nonce, ciphertext, aad)
        except InvalidTag as exc:
            if frame_end == total:
                break  # corrupt tail: tolerated like truncation
            raise VaultIntegrityError(
                f"Frame stream {target} is corrupt at offset {offset}"
            ) from exc
        offset = frame_end


def decode_frame_at(blob: bytes, offset: int, *, vault, purpose: str) -> Optional[tuple[bytes, int]]:
    """``(payload, end_offset)`` when a complete, authenticating frame starts at *offset*, else None."""

    if offset + _LENGTH.size > len(blob):
        return None
    (size,) = _LENGTH.unpack_from(blob, offset)
    start = offset + _LENGTH.size
    end = start + size
    if size < _NONCE_BYTES + 16 or end > len(blob):
        return None
    aad = f"hermes-frames|1|{purpose}".encode("utf-8")
    try:
        payload = AESGCM(_frame_key(vault, purpose)).decrypt(
            blob[start : start + _NONCE_BYTES], blob[start + _NONCE_BYTES : end], aad)
    except InvalidTag:
        return None
    return payload, end


def split_stream(blob: bytes, *, vault, purpose: str) -> tuple[list[bytes], int]:
    """Decrypt the longest valid frame prefix of *blob*: ``(payloads, consumed)``."""

    payloads: list[bytes] = []
    offset = 0
    while (frame := decode_frame_at(blob, offset, vault=vault, purpose=purpose)) is not None:
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
            payloads, _ = split_stream(blob, vault=vault, purpose=purpose)
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
