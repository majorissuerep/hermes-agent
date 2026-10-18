"""Provider-neutral audio container repair for local speech tools."""
from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)


def transcode_to_ogg_opus(path: str, *, bitrate: str = "32k", timeout: int = 60,
                          output_path: str | None = None) -> str | None:
    """Best-effort ffmpeg transcode to Ogg/Opus (voip-tuned) for native voice bubbles: the written
    ``.ogg`` path (a NEW temp file unless ``output_path`` is given; caller cleans up), or None when
    ffmpeg is missing/fails. ``output_path`` may equal ``path`` (in-place container repair) — the
    encode goes through a sidecar so a failed run never truncates the source. Blocking (to_thread)."""
    from hermes_platform.resolver import locate_command
    command = locate_command("ffmpeg").command
    ffmpeg = command[0] if command else None
    if not ffmpeg:
        return None
    if output_path is None:
        fd, ogg_path = tempfile.mkstemp(prefix="voice_transcode_", suffix=".ogg")
        os.close(fd)
    else:
        ogg_path = output_path
    in_place = os.path.abspath(str(path)) == os.path.abspath(ogg_path)
    work_path = ogg_path + ".tmp.ogg" if in_place else ogg_path
    try:
        result = subprocess.run(
            [ffmpeg, "-v", "error", "-y", "-i", str(path),
             "-acodec", "libopus", "-ac", "1", "-b:a", bitrate, "-vbr", "on",
             "-application", "voip", "-compression_level", "10", "-f", "ogg", work_path],
            capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL, check=False)
        if result.returncode == 0 and os.path.getsize(work_path) > 0:
            if in_place:
                os.replace(work_path, ogg_path)
            return ogg_path
        logger.warning("ffmpeg Ogg/Opus transcode of %s failed (returncode=%s): %s", path, result.returncode,
                       (result.stderr or b"").decode("utf-8", errors="replace")[:500])
    except Exception:
        logger.warning("voice transcode to Ogg/Opus failed for %s", path, exc_info=True)
    with contextlib.suppress(OSError):
        os.unlink(work_path)
    return None
