"""Durable, profile-encrypted provider deliverables; never remote upload staging.

Delivery URLs can expire, and base64 output may have no other copy. Keep the
original bytes in authenticated artifacts and decode only for an authorized consumer.
"""

from __future__ import annotations

import base64
import datetime
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

from hermes_security import artifacts

# Never age-prune these deliverables: base64-returning providers may have no other copy.
GENERATED_SUBDIR = "generated"

_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
_MAX_SAVE_URL_REDIRECTS = 5
_GENERATION_CUSTODY: ContextVar[tuple | None] = ContextVar("generated_media_custody", default=None)


def _output_custody(home=None):
    from hermes_constants import get_hermes_home

    selected = Path(get_hermes_home() if home is None else home).resolve()
    active = _GENERATION_CUSTODY.get()
    if active is not None:
        if active[0] != selected:
            raise PermissionError("Generated output belongs to a different profile")
        active[1].check()
        return active
    return selected, artifacts._bound_custody(selected)


@contextmanager
def generation_scope():
    """Pin original custody before invoking a provider, including its later callbacks."""
    active = _output_custody()
    token = _GENERATION_CUSTODY.set(active)
    try:
        yield
    finally:
        _GENERATION_CUSTODY.reset(token)


def cache_dir(kind: str) -> Path:
    """Return the strict encrypted artifact directory for one provider media kind."""
    from hermes_constants import get_hermes_home

    home = get_hermes_home()
    path = _directory(home, kind)
    selected, relative = artifacts._location(path / ".location", home=home)
    artifacts._bound_custody(selected)
    with artifacts._parent(selected, relative, create=True):
        pass
    return path


def _directory(home: Path, kind: str) -> Path:
    media = kind.removeprefix(GENERATED_SUBDIR + "/")
    if media not in {"images", "videos"}:
        raise ValueError("Only generated image/video output kinds are supported")
    return home / "artifacts" / GENERATED_SUBDIR / media


def cache_path(kind: str, prefix: str, extension: str, *, home: Path | None = None) -> Path:
    from hermes_constants import get_hermes_home

    prefix = re.sub(r"[^A-Za-z0-9_-]", "_", prefix)
    if not prefix or not re.fullmatch(r"[A-Za-z0-9]{1,12}", extension):
        raise ValueError("Invalid generated media filename")
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    return _directory(get_hermes_home() if home is None else home, kind) / f"{prefix}_{ts}_{short}.{extension}"


def save_bytes(kind: str, raw: bytes, *, prefix: str, extension: str) -> Path:
    """Atomically retain the only copy as ciphertext; missing/locked stores refuse."""
    home, custody = _output_custody()
    path = cache_path(kind, prefix, extension, home=home)
    return artifacts.write_bytes(path, raw, home=home, custody=custody)


def save_b64(kind: str, b64_data: str, *, prefix: str, extension: str) -> Path:
    """Decode base64 data into the cache and return the absolute path."""
    home, custody = _output_custody()
    path = cache_path(kind, prefix, extension, home=home)
    relative = artifacts._location(path, home=home)[1]
    maximum = artifacts._plaintext_bound(relative)
    if len(b64_data) > ((maximum + 2) // 3) * 4:
        raise ValueError("Generated media exceeds its byte limit")
    return artifacts.write_bytes(path, base64.b64decode(b64_data, validate=True), home=home, custody=custody)


def save_url(
    kind: str, url: str, *, prefix: str, timeout: float, max_bytes: int, chunk_size: int,
    content_types: dict[str, str], url_extensions: tuple[str, ...], default_extension: str,
    label: str, empty_error: str, headers: Optional[dict[str, str]] = None,
    require_known_content_type: bool = False, trusted_origin: bool = False,
) -> Path:
    """Stream-download *url* into the cache with a size cap.

    The extension comes from the response ``Content-Type`` (an explicit table —
    never inherit a type pointing at HTML/JSON from a degenerate response), then
    the URL suffix (some CDNs return ``application/octet-stream``), then
    *default_extension*. Raises on any network / HTTP / oversize / empty error so
    callers can fall back to the bare URL; a partial file is never left behind.

    The URL is provider-supplied, so every hop is validated with
    ``tools.url_safety`` before a socket opens (and again at TCP connect by the
    guarded transport, closing DNS-rebinding TOCTOU). Caller-supplied *headers*
    (e.g. provider auth) go to the first hop only — a redirect target never
    receives them.

    *trusted_origin* is for callers that built *url* from the operator's own
    provider ``base_url`` (not from a provider response): the first hop skips the
    private-address class check so a LAN/loopback relay works without
    ``security.allow_private_urls``, but the cloud-metadata floor still applies and
    every redirect target is re-validated in full.
    """
    import httpx

    from tools.url_safety import create_ssrf_safe_client, is_always_blocked_url, is_safe_url

    from hermes_constants import get_hermes_home

    home = get_hermes_home()
    custody = None
    current_url, hop_headers, trusted_hop = url, headers, trusted_origin
    for _ in range(_MAX_SAVE_URL_REDIRECTS + 1):
        if trusted_hop:
            if is_always_blocked_url(current_url):
                raise ValueError(f"{label} URL targets an always-blocked address: {current_url}")
            client = httpx.Client(timeout=timeout, follow_redirects=False)
        else:
            if not is_safe_url(current_url):
                raise ValueError(f"{label} URL failed the SSRF safety check: {current_url}")
            client = create_ssrf_safe_client(timeout=timeout, follow_redirects=False)
        with client:
            custody = _output_custody(home)[1] if custody is None else custody
            custody.check()
            with client.stream("GET", current_url, headers=hop_headers) as response:
                if response.status_code in _REDIRECT_STATUS_CODES:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError(f"{label} download redirected without a Location: {current_url}")
                    current_url, hop_headers, trusted_hop = urljoin(current_url, location), None, False
                    continue
                if not response.is_success:
                    response.read()
                    response.raise_for_status()

                content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                extension = content_types.get(content_type)
                if require_known_content_type and extension is None:
                    raise ValueError(
                        f"{label} download returned unexpected Content-Type {content_type or '(missing)'}"
                    )
                if extension is None:
                    url_path = current_url.split("?", 1)[0].lower()
                    extension = next(
                        ("jpg" if ext == "jpeg" else ext for ext in url_extensions if url_path.endswith(f".{ext}")),
                        default_extension,
                    )
                path = cache_path(kind, prefix, extension, home=home)
                maximum = min(max_bytes, artifacts._plaintext_bound(path.relative_to(home)))
                raw = _download_body(response, maximum, chunk_size, label, url, empty_error)
                return artifacts.write_bytes(path, raw, home=home, custody=custody)
    raise ValueError(f"{label} download exceeded {_MAX_SAVE_URL_REDIRECTS} redirects: {url}")


def _download_body(response, maximum, chunk_size, label, url, empty_error):
    chunks, total = [], 0
    for chunk in response.iter_bytes(chunk_size=chunk_size):
        total += len(chunk)
        if total > maximum:
            raise ValueError(f"{label} at {url} exceeds {maximum // (1024 * 1024)}MB cap; refusing to cache.")
        chunks.append(chunk)
    if not total:
        raise ValueError(empty_error.format(url=url))
    return b"".join(chunks)
