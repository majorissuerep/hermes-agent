"""Profile-scoped media artifacts for local tools and MCP results."""

from __future__ import annotations

import os
from pathlib import Path
import re
import uuid

from hermes_constants import get_hermes_dir

MAX_MEDIA_BYTES = 128 * 1024 * 1024


def _write_media(kind: str, prefix: str, extension: str, data: bytes) -> str:
    if len(data) > MAX_MEDIA_BYTES:
        raise ValueError(f"Media artifact exceeds {MAX_MEDIA_BYTES} bytes")
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,12}", extension):
        raise ValueError("Invalid media extension")
    directory = get_hermes_dir(f"cache/{kind}", f"{kind.rstrip('s')}_cache")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    from hermes_cli.config import _secure_dir

    _secure_dir(directory)
    path = directory / f"{prefix}_{uuid.uuid4().hex}{extension}"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return str(path)


def cache_image_from_bytes(data: bytes, ext: str = ".jpg") -> str:
    image = len(data) >= 4 and (
        data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff"
        or data[:6] in {b"GIF87a", b"GIF89a"} or data[:2] == b"BM"
        or (data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP")
    )
    if not image:
        raise ValueError("Refusing to cache a non-image payload")
    return _write_media("images", "img", ext, data)


def cache_audio_from_bytes(data: bytes, ext: str = ".ogg") -> str:
    from tools.audio_container import sniff_audio_ext

    return _write_media("audio", "audio", sniff_audio_ext(data, ext), data)


def cache_document_from_bytes(data: bytes, filename: str) -> str:
    extension = Path(filename or "document").suffix
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,12}", extension):
        extension = ".bin"
    return _write_media("documents", "doc", extension, data)
