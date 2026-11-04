"""Image format detection, normalization and region cropping for vision tools.

Everything here runs BEFORE an image is base64-embedded: a vision tool result is
baked into immutable history and re-sent every turn, so an unsupported media type
or corrupt bytes would wedge the session with a non-retryable 400 on every resume.

Private pixels never become a plaintext file here: helpers take either a caller-owned path
or in-memory bytes and return bytes. Only the file-based SVG rasterizer fallbacks need a
filesystem, and they run inside a proven RAM-only protected workspace or not at all.
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("tools.vision_tools")

# A caller-owned image file (read, never modified) or the image bytes themselves.
ImageInput = Path | bytes

_EXTENSION_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}

# Media types the major vision providers (Anthropic in particular) accept
# inline. SVG/BMP/TIFF are rejected with a non-retryable 400.
_ANTHROPIC_SUPPORTED_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})


def unsupported_inline_image_media_type(url: str) -> Optional[str]:
    """``image/<subtype>`` of a ``data:image/...`` URL the inline-image wire paths reject
    (``image/jpg`` counts as JPEG); None for accepted rasters and for non-data URLs (the
    provider owns remote-URL validation)."""
    header = url.partition(",")[0].lower()
    if not header.startswith("data:image/"):
        return None
    subtype = header[len("data:image/"):].split(";", 1)[0].strip() or "unknown"
    media_type = "image/jpeg" if subtype == "jpg" else f"image/{subtype}"
    return None if media_type in _ANTHROPIC_SUPPORTED_MEDIA_TYPES else media_type


_MAGIC_MIME_TYPES = (
    (b"\xff\xd8\xff", "image/jpeg"), ((b"GIF87a", b"GIF89a"), "image/gif"), (b"BM", "image/bmp"),
)


def _determine_mime_type(image_path: Path) -> str:
    """MIME type from file extension (defaults to image/jpeg)."""
    return _EXTENSION_MIME_TYPES.get(image_path.suffix.lower(), "image/jpeg")


def _image_bytes(image: ImageInput) -> bytes:
    return image if isinstance(image, bytes) else Path(image).read_bytes()


def _fallback_mime(image: ImageInput) -> str:
    """Extension MIME for a path; in-memory bytes carry no name, so the same image/jpeg default."""
    return "image/jpeg" if isinstance(image, bytes) else _determine_mime_type(Path(image))


def _as_file(image: ImageInput):
    """What ``PIL.Image.open`` reads: the path itself, or an in-memory buffer over the bytes."""
    return BytesIO(image) if isinstance(image, bytes) else image


def _open_image(image: ImageInput):
    """``PIL.Image.open`` over a path or in-memory bytes (raises ImportError without Pillow)."""
    from PIL import Image
    return Image.open(_as_file(image))


def _detect_image_mime_type_from_bytes(data: bytes) -> Optional[str]:
    """Magic-byte MIME sniff (authoritative; no extension trust). ``None`` for anything without a
    recognized header — including SVG, which has none (the resolver sniffs ``<svg`` itself)."""
    header = data[:64]
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        # Reject corrupt PNGs before they can be embedded. Pillow is optional —
        # without it fall back to header-only sniffing; only a failed verify() rejects.
        try:
            from PIL import Image
        except ImportError:
            return "image/png"
        try:
            with Image.open(BytesIO(data)) as image:
                image.verify()
            return "image/png"
        except Exception:
            return None
    for magic, mime in _MAGIC_MIME_TYPES:
        if header.startswith(magic):
            return mime
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    # HEIF/HEIC/AVIF — ISO-BMFF container: bytes 0-4 are the box size, 4-8 the box type 'ftyp',
    # 8-12 the major brand, and every 4 bytes from 16 a compatible brand. iPhone photos are HEIC
    # (often mislabeled .jpg by upload pipelines); vision providers don't ingest HEIF, but
    # _normalize_to_supported_image re-encodes it to PNG via pillow-heif (same soft-dependency
    # pattern as SVG). The major brand alone can't tell AVIF from HEIC: AVIF files routinely carry
    # generic 'mif1' as major and identify the codec only in the compatible-brand list.
    if len(header) >= 12 and header[4:8] == b"ftyp":
        major = header[8:12]
        # Bound the brand scan by the declared box size so brands are never read out of a FOLLOWING
        # box. A malformed size fails CLOSED (no compatible brands scanned) — falling back to the
        # whole sniff window would widen the scan exactly when an attacker controls the size field.
        # A size >= 16 overrunning the sniffed window is merely a truncated read: clamp it.
        box_size = int.from_bytes(header[:4], "big")
        limit = 16 if box_size < 16 else min(box_size, len(header))
        brands = {major} | {header[i:i + 4] for i in range(16, limit - 3, 4)}
        # AV1-coded brands win over the generic HEIF ones when both appear.
        if brands & {b"avif", b"avis", b"av01"}:
            return "image/avif"
        if brands & {
            b"heic", b"heix", b"heim", b"heis",  # HEVC-coded HEIF still/sequence
            b"hevc", b"hevx",
            b"mif1", b"msf1",                    # generic HEIF image / sequence
        }:
            return "image/heic"
    return None


def _supported_media_types() -> frozenset:
    """Formats the ACTIVE main model's server can decode. The managed llama-server decodes with
    stb_image — no WebP — and an undecodable image part fails SILENTLY (the model confabulates),
    so the set is narrowed there and normalization converts those formats to PNG."""
    try:
        from agent.auxiliary_client import _runtime_main_value as _v
        from hermes_cli.local_runtime.capabilities import ACCEPTED_IMAGE_MIMES, is_managed_provider
        if is_managed_provider(str(_v("provider") or ""), str(_v("base_url") or "")):
            return ACCEPTED_IMAGE_MIMES
    except Exception:  # best-effort narrowing only
        pass
    return _ANTHROPIC_SUPPORTED_MEDIA_TYPES


def _nonempty_file(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _cairosvg_png(svg: bytes) -> Optional[bytes]:
    """SVG -> PNG fully in memory via cairosvg (soft dependency); None when absent or failing."""
    try:
        import cairosvg  # type: ignore
    except ImportError:
        return None
    try:
        png = cairosvg.svg2png(bytestring=svg)
    except Exception:  # cairosvg surfaces parser, cairo and resource errors of many types
        logger.debug("cairosvg could not render the SVG in memory", exc_info=True)
        return None
    return png or None


def _rasterize_svg_to_png(svg_path: Path, out_path: Path) -> bool:
    """File-based SVG -> PNG fallbacks: svglib+reportlab, rsvg-convert, inkscape (all soft deps).
    Only :func:`_rasterize_svg_bytes` calls this, with both paths inside a protected RAM workspace."""
    try:
        from svglib.svglib import svg2rlg  # type: ignore
        from reportlab.graphics import renderPM  # type: ignore
        drawing = svg2rlg(str(svg_path))
        if drawing is not None:
            renderPM.drawToFile(drawing, str(out_path), fmt="PNG")
            return _nonempty_file(out_path)
    except Exception:
        pass
    import shutil
    import subprocess
    for cmd in (
        ["rsvg-convert", "-o", str(out_path), str(svg_path)],
        ["inkscape", str(svg_path), "--export-type=png", f"--export-filename={out_path}"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, check=True, capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
                if _nonempty_file(out_path):
                    return True
            except Exception:
                continue
    return False


def _rasterize_svg_bytes(svg: bytes) -> Optional[bytes]:
    """SVG -> PNG bytes, or None when no rasterizer is installed. cairosvg renders in memory; the
    file-based fallbacks get the SVG only inside a proven RAM-only workspace, removed afterwards.
    Raises ``MediaCustodyError`` when a fallback is needed but no such workspace exists — the SVG
    is then never written anywhere."""
    png = _cairosvg_png(svg)
    if png is not None:
        return png
    from tools.browser_media_custody import protected_media_workspace
    with protected_media_workspace("vision-svg") as workspace:
        svg_path, png_path = workspace / "image.svg", workspace / "image.png"
        svg_path.write_bytes(svg)
        return png_path.read_bytes() if _rasterize_svg_to_png(svg_path, png_path) else None


def rasterize_svg_data_url(url: str) -> Optional[str]:
    """``data:image/svg+xml[;base64],...`` → ``data:image/png;base64,...`` through the same
    rasterizers vision_analyze uses; None when the payload does not decode, no rasterizer is
    available, or a file-based rasterizer would need a RAM workspace this host cannot prove.
    Request-path callers decide the fallback (Responses backends 400 on SVG source, so the caller
    must never forward the SVG itself)."""
    import base64
    from urllib.parse import unquote
    header, _, payload = url.partition(",")
    try:
        raw = base64.b64decode(payload) if ";base64" in header.lower() else unquote(payload).encode()
    except Exception:
        return None
    from hermes_security.errors import VaultError
    from tools.browser_media_custody import MediaCustodyError
    try:
        png = _rasterize_svg_bytes(raw)
    except (MediaCustodyError, VaultError, OSError) as exc:  # the caller's placeholder is the fallback
        logger.warning("Inline SVG left unrasterized (no plaintext file is written for it): %s", exc)
        return None
    return None if png is None else "data:image/png;base64," + base64.b64encode(png).decode("ascii")


_SVG_NO_RASTERIZER = (
    "This is an SVG, which vision models cannot read directly, and no "
    "SVG rasterizer is installed (tried cairosvg, svglib, rsvg-convert, "
    "inkscape). Convert the SVG to PNG first — e.g. open it in a browser "
    "and screenshot it — then re-run vision_analyze on the PNG.")


def _normalize_svg(svg: bytes) -> tuple[Optional[bytes], Optional[str], Optional[str]]:
    from tools.browser_media_custody import MediaCustodyError
    try:
        png = _rasterize_svg_bytes(svg)
    except MediaCustodyError as exc:
        return None, None, (
            "This is an SVG, which vision models cannot read directly. cairosvg (which renders in "
            "memory) is not installed, and the file-based rasterizers only run in a proven RAM-only "
            f"workspace: {exc} Alternatively install cairosvg, or convert the SVG to PNG first.")
    if png is None:
        return None, None, _SVG_NO_RASTERIZER
    return png, "image/png", None


def _normalize_to_supported_image(
    image: ImageInput, detected_mime: str) -> tuple[Optional[bytes], Optional[str], Optional[str]]:
    """Ensure an image is in a provider-supported format, in memory. Returns ``(data, mime, error)``:
    the original bytes when supported; ``(png_bytes, "image/png", None)`` after conversion;
    ``(None, None, message)`` when impossible. SVG is rasterized; other Pillow-readable rasters
    (BMP, TIFF, HEIC, AVIF) re-encode to PNG. No derivative file is created."""
    data = _image_bytes(image)
    if detected_mime in _supported_media_types():
        return data, detected_mime, None
    if detected_mime == "image/svg+xml":
        return _normalize_svg(data)
    # HEIF/AVIF need a codec Pillow's core doesn't always carry, served by DIFFERENT optional
    # backends that must not be gated on one another: HEIC/HEIF (HEVC) needs the pillow-heif
    # plugin registered; AVIF (AV1) is native in Pillow >= 11.3 while pillow-heif wheels are
    # frequently built with NO AV1 codec at all. Register whatever backend is available, let the
    # decode attempt be the arbiter, and only emit a codec-specific error if it really fails —
    # same soft-dependency posture as the SVG branch.
    if detected_mime in ("image/heic", "image/avif"):
        try:
            import pillow_heif  # type: ignore
            pillow_heif.register_heif_opener()
        except Exception:
            logger.debug("pillow-heif unavailable; relying on Pillow for %s", detected_mime)
    try:
        out = BytesIO()
        with _open_image(data) as _img:
            if _img.mode not in ("RGB", "RGBA", "L"):
                _img = _img.convert("RGBA")
            _img.save(out, format="PNG")
        if out.getbuffer().nbytes:
            return out.getvalue(), "image/png", None
    except Exception as _exc:
        logger.warning("Failed to normalize %s image to PNG: %s", detected_mime, _exc)
        # Codec-specific guidance: name the backend that actually serves this format.
        if detected_mime == "image/heic":
            return None, None, (
                "This is a HEIC/HEIF image (common for iPhone photos), which "
                "vision models cannot read directly, and no HEIF decoder is "
                "available. Install one (`pip install pillow-heif`) and re-run "
                "vision_analyze, or convert the image to PNG/JPEG first.")
        if detected_mime == "image/avif":
            return None, None, (
                "This is an AVIF image, which vision models cannot read "
                "directly, and no AV1 decoder is available. Upgrade Pillow "
                "(>= 11.3 bundles AVIF support) or install a pillow-heif build "
                "with an AV1 codec, then re-run vision_analyze — or convert the "
                "image to PNG/JPEG first.")
    return None, None, (
        f"Image format {detected_mime!r} is not supported by the vision API "
        f"and could not be converted to PNG (install Pillow for raster "
        f"conversion). Convert it to PNG or JPEG and try again.")


# Full raster validation runs on untrusted images in a shared CPU executor: bound animated
# work by frame count AND total decoded area so a compact file cannot monopolize a worker.
_VISION_MAX_VALIDATED_FRAME_COUNT = 100
_VISION_MAX_VALIDATED_AGGREGATE_PIXELS = 100_000_000


def _validate_raster_image_decodable(
    image: ImageInput,
    max_frames: int = _VISION_MAX_VALIDATED_FRAME_COUNT,
    max_pixels: int = _VISION_MAX_VALIDATED_AGGREGATE_PIXELS) -> Optional[str]:
    """Return an error unless Pillow can fully decode every frame. Header sniffing and ``Image.open``
    only inspect containers: a timed-out download can look like a valid PNG with a truncated pixel
    stream. Without Pillow the image passes unvalidated rather than rejecting everything."""
    try:
        from PIL import ImageSequence as _PILImageSequence
    except ImportError:
        return None
    try:
        with _open_image(image) as opened:
            opened.verify()
        with _open_image(image) as opened:
            validated_pixels = 0
            for frame_number, frame in enumerate(_PILImageSequence.Iterator(opened), start=1):
                if frame_number > max_frames:
                    return (
                        "Image validation rejected animation: "
                        f"frame {frame_number} exceeds the maximum "
                        f"{max_frames} validated frames.")
                next_validated_pixels = validated_pixels + frame.width * frame.height
                if next_validated_pixels > max_pixels:
                    return (
                        "Image validation rejected animation: aggregate decoded "
                        f"pixel count would reach {next_validated_pixels} at frame "
                        f"{frame_number}, exceeding the maximum "
                        f"{max_pixels}.")
                frame.load()
                validated_pixels = next_validated_pixels
    except Exception as exc:
        return f"Image could not be fully decoded: {exc}"
    return None


def _image_exceeds_dimension(image: ImageInput, max_dimension: int) -> bool:
    """True if the longest side exceeds ``max_dimension`` px (Anthropic's 8000px per-side cap is
    independent of bytes). False without Pillow or on unreadable files — a missing soft
    dependency must never break the embed path."""
    try:
        with _open_image(image) as _img:
            return max(_img.size) > max_dimension
    except Exception:
        return False


def _crop_image_region(
    image: ImageInput, region: Any, offset_out: Optional[dict] = None
) -> tuple[Optional[bytes], Optional[str], Optional[str]]:
    """Crop to ``region`` = [x1, y1, x2, y2] (original-image pixels), BEFORE downscaling so the crop
    gets the full resolution budget. Coordinates clamp to the image bounds; a zero-area/inverted
    region is rejected with an error naming the real dimensions. Returns ``(png_bytes, mime, None)``
    — in memory, no derivative file — or ``(None, None, error)``.
    Ported from QwenLM/qwen-code zoom-image.ts (Apache-2.0)."""
    try:
        from PIL import Image
    except ImportError:
        return None, None, (
            "region cropping requires Pillow (run `hermes pm repair`); "
            "retry without the region parameter.")
    if not (isinstance(region, (list, tuple)) and len(region) == 4
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in region)):
        return None, None, (
            "Invalid region: expected [x1, y1, x2, y2] as four numbers "
            "(pixel coordinates in the original image).")
    try:
        with Image.open(_as_file(image)) as img:
            width, height = img.size
            x1, y1, x2, y2 = (int(v) for v in region)
            cx1, cy1, cx2, cy2 = (max(0, min(v, b)) for v, b in zip((x1, y1, x2, y2), (width, height) * 2))
            if cx2 <= cx1 or cy2 <= cy1:
                return None, None, (
                    f"Invalid region [{x1}, {y1}, {x2}, {y2}]: crops to zero "
                    f"area after clamping to the image bounds. The image is "
                    f"{width}x{height} px — pick x1<x2 and y1<y2 inside "
                    f"[0, 0, {width}, {height}].")
            cropped = img.crop((cx1, cy1, cx2, cy2))
            if offset_out is not None:
                offset_out.update(x=cx1, y=cy1, width=cx2 - cx1, height=cy2 - cy1)
            if cropped.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                cropped = cropped.convert("RGB")
            out = BytesIO()
            cropped.save(out, format="PNG")
            return out.getvalue(), "image/png", None
    except Exception as exc:
        return None, None, f"Failed to crop region: {exc}"
