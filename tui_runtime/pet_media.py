"""Private stdio pet media payloads and encrypted generation staging.

Shared payload caching is reserved for public installed catalog assets. Generated
pets carry original custody through every decode, frame and payload return.
"""
from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path
import threading
import time

logger = logging.getLogger(__name__)

_pet_payload_cache_lock = threading.Lock()


_pet_payload_cache: dict[tuple, dict] = {}


def sheet_revision(spritesheet) -> str:
    """Stable revision id for one spritesheet file."""
    with contextlib.suppress(Exception):
        stat = spritesheet.stat()
        return f"{stat.st_mtime_ns}:{stat.st_size}"
    return "0:0"


def _clone_payload(payload: dict) -> dict:
    """Shallow-clone cached payloads so callers can't mutate shared state."""
    out = dict(payload)
    for key, kind in (("framesByState", dict), ("framesByRow", dict), ("stateRows", list)):
        if isinstance(payload.get(key), kind):
            out[key] = kind(payload[key])
    return out


def _row_frame_counts(spritesheet) -> dict:
    """Real frame count per concrete spritesheet row name."""
    with contextlib.suppress(Exception):
        from agent.pet import constants, render
        from agent.pet.private_media import rgba

        image = rgba(spritesheet)
        W, H = constants.FRAME_W, constants.FRAME_H
        cols = max(1, image.width // W)
        row_count = max(1, image.height // H)
        rows = constants.state_rows_for_grid(row_count)
        out: dict[str, int] = {}
        for row_idx, name in enumerate(rows[:row_count]):
            top = row_idx * H
            blank = lambda col: render._frame_is_blank(image.crop((col * W, top, col * W + W, top + H)))
            out[name] = next((col for col in range(cols) if blank(col)), cols)  # frames before the first blank cell
        return out
    return {}


def sprite_payload(pet, *, scale: float) -> dict:
    """Renderer payload (spritesheet bytes + geometry) for *pet* — one shape for ``pet.info`` (active
    mascot) and ``pet.hatch`` (unadopted preview)."""
    import base64
    from agent.pet import constants
    from agent.pet.private_media import read_image

    custody = getattr(pet, "_private_custody", None)
    raw = read_image(pet.spritesheet, custody=custody)
    try:
        stat = pet.spritesheet.stat()
        cache_key = (str(pet.spritesheet), stat.st_mtime_ns, stat.st_size, pet.slug, pet.display_name, round(scale, 4))
        if custody is not None:
            cache_key = None  # Only public catalog assets enter the shared payload cache.
    except OSError:
        cache_key = None
    if cache_key is not None:
        with _pet_payload_cache_lock:
            cached = _pet_payload_cache.get(cache_key)
        if cached is not None:
            return _clone_payload(cached)
    try:  # real (padding-trimmed) frame count per state; {} → the canvas uses the static framesPerState
        from agent.pet import render
        frames_by_state = render.state_frame_counts(str(pet.spritesheet), custody=custody)
    except (ImportError, OSError, ValueError, TypeError):
        frames_by_state = {}
    mime = "image/png" if pet.spritesheet.suffix.lower() == ".png" else "image/webp"
    payload = {
        "slug": pet.slug, "displayName": pet.display_name, "mime": mime,
        "spritesheetBase64": base64.standard_b64encode(raw).decode("ascii"),
        "spritesheetRevision": sheet_revision(pet.spritesheet), "frameW": constants.FRAME_W,
        "frameH": constants.FRAME_H, "framesPerState": constants.FRAMES_PER_STATE,
        "framesByState": frames_by_state,
        "framesByRow": _row_frame_counts(raw), "loopMs": constants.LOOP_MS,
        "scale": scale, "stateRows": _state_rows(raw),
    }
    if cache_key is not None:
        with _pet_payload_cache_lock:
            _pet_payload_cache[cache_key] = payload
            while len(_pet_payload_cache) > 8:
                _pet_payload_cache.pop(next(iter(_pet_payload_cache)))
    if custody is not None:
        from agent.pet.private_media import _check_scope

        _check_scope(custody)
    return _clone_payload(payload)


def _state_rows(spritesheet) -> list[str]:
    """Row taxonomy for the concrete sheet (legacy 8-row or current 9-row atlas), in the renderer's `PetState` names."""
    from agent.pet import constants
    with contextlib.suppress(Exception):
        from agent.pet.private_media import rgba

        row_count = max(1, rgba(spritesheet).height // constants.FRAME_H)
        return list(constants.state_rows_for_grid(row_count))
    return list(constants.STATE_ROWS)


def generation_root():
    """Profile-scoped staging dir for in-progress generation drafts."""
    from agent.provider_media import _output_custody
    from hermes_security import artifacts

    home, custody = _output_custody()
    relative = Path("artifacts/generated/pet-drafts")
    with artifacts._parent(home, relative / ".location", create=True):
        custody.check()
    root = home / relative
    return root


def sweep_drafts(root, *, max_age_s: float = 3600.0) -> None:
    """Drop stale draft staging dirs so cache never grows unbounded."""
    import shutil
    try:
        now = time.time()
        for child in (c for c in root.iterdir() if c.is_dir() and now - c.stat().st_mtime > max_age_s):
            shutil.rmtree(child, ignore_errors=True)
    except Exception as exc:
        logger.debug("pet-gen sweep failed: %s", exc)


def png_data_uri(path, *, max_px: int = 160) -> str:
    """Downscaled PNG data URI for a draft image (small preview payload)."""
    import base64, io
    from PIL import Image
    from agent.pet.private_media import rgba

    img = rgba(path)
    img.thumbnail((max_px, max_px), Image.LANCZOS)
    img.save(buf := io.BytesIO(), format="PNG")
    return "data:image/png;base64," + base64.standard_b64encode(buf.getvalue()).decode("ascii")


_PET_REFERENCE_MIME_EXT = {"png": "png", "jpeg": "jpg", "jpg": "jpg", "webp": "webp", "gif": "gif"}


try:
    _PET_REFERENCE_MAX_BYTES = max(1, int(os.environ.get("HERMES_PET_REFERENCE_MAX_BYTES") or str(16 * 1024 * 1024)))
except (TypeError, ValueError):
    _PET_REFERENCE_MAX_BYTES = 16 * 1024 * 1024


def reference_images_from_data_url(ref_raw: str, stage) -> list:
    """Decode + validate a reference-image data URL into the stage dir."""
    import base64, binascii
    import re as _re
    match = _re.match(r"^data:image/([a-zA-Z0-9.+-]+);base64,(.*)$", ref_raw, _re.DOTALL)
    if not match:
        raise ValueError("invalid reference image format")
    if (ext := _PET_REFERENCE_MIME_EXT.get(match.group(1).lower())) is None:
        raise ValueError("unsupported reference image type")
    payload = "".join(match.group(2).split())
    if (len(payload) * 3) // 4 > _PET_REFERENCE_MAX_BYTES:
        raise ValueError("reference image too large")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid reference image data") from exc
    if len(raw) > _PET_REFERENCE_MAX_BYTES:
        raise ValueError("reference image too large")
    from agent.provider_media import _output_custody
    from hermes_security import artifacts

    home, custody = _output_custody()
    ref_path = artifacts.write_bytes(stage / f"reference.{ext}", raw, home=home, custody=custody)
    return [ref_path]
