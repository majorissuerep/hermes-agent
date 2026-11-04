"""Ordinary vision/video media is prepared without a plaintext copy on persistent storage.

A downloaded, decoded, normalized or cropped image — and a downloaded video — used to be
written to ``$HERMES_HOME/cache/vision``/``cache/video`` or a system temp file while the
provider read it. Preparation is now in memory. Only the file-based SVG rasterizer fallbacks
need a filesystem; they run in a proven RAM-only protected workspace, and with no such
workspace they fail closed with guidance instead of writing the SVG anywhere.

Only external boundaries are faked: the HTTP server (an ``httpx.AsyncClient`` stand-in), DNS
safety answers, the vision provider call, the SVG rasterizer programs, and the RAM proof (the
labelled unit seam ``tools.browser_media_custody._prove``, not evidence of a real mount).
"""
from __future__ import annotations

import base64
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_constants import get_hermes_home
from hermes_platform.memory_protection import RamProof

Image = pytest.importorskip("PIL.Image")

VIDEO_CANARY = b"synthetic-private-video-canary-91c2"
SVG_CANARY = b"synthetic-private-svg-canary-5d07"
SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"><desc>'
       + SVG_CANARY + b"</desc></svg>")
_IMAGE_MAGIC = (b"\x89PNG", b"BM", b"\xff\xd8\xff")


def _png_bytes(size=(4, 4)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (9, 99, 199)).save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """Persistent locations a plaintext copy could land: the profile home and the temp dir."""
    temp = tmp_path / "system-tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setenv("TERMINAL_ENV", "local")
    return [get_hermes_home(), temp]


def _files(roots, predicate) -> list[Path]:
    return [p for root in roots for p in Path(root).rglob("*") if p.is_file() and predicate(p.read_bytes())]


def _image_files(roots) -> list[Path]:
    return _files(roots, lambda data: data.startswith(_IMAGE_MAGIC))


def _http_server(body: bytes, url: str, mid_stream=lambda: None):
    """An ``httpx.AsyncClient`` stand-in that streams ``body`` for one GET in two chunks,
    calling ``mid_stream`` between them."""
    class _Response:
        headers: dict = {}

        def __init__(self):
            self.url = url

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield body[: len(body) // 2]
            mid_stream()
            yield body[len(body) // 2:]

    class _Stream:
        async def __aenter__(self):
            return _Response()

        async def __aexit__(self, *exc):
            return False

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.stream.return_value = _Stream()
    return patch("tools.vision_tools.httpx.AsyncClient", return_value=client)


def _provider(seen: list, snapshot):
    async def call(**kwargs):
        seen.append((kwargs["messages"][0]["content"][1], snapshot()))
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "described"
        return response
    return call


@pytest.mark.asyncio
async def test_downloaded_image_is_normalized_and_cropped_without_a_plaintext_file(roots):
    """Neither mid-download nor while the provider reads an HTTP image that needed BMP→PNG
    normalization and a region crop does a file exist for it under the temp dir or the profile
    home — and the model still gets exactly the requested crop."""
    from tools.vision_tools import vision_analyze_tool
    bmp = io.BytesIO()
    Image.new("RGB", (40, 20), (200, 30, 30)).save(bmp, "BMP")
    url = "https://media.test/private-scan.bmp"
    seen: list = []
    mid_download: list = []
    with (
        _http_server(bmp.getvalue(), url, lambda: mid_download.extend(Path(roots[1]).rglob("*"))),
        patch("tools.url_safety.is_safe_url", return_value=True),
        patch("tools.website_policy.check_website_access", return_value=None),
        patch("tools.vision_tools.check_website_access", return_value=None),
        patch("tools.vision_tools.async_call_llm", side_effect=_provider(seen, lambda: _image_files(roots))),
    ):
        result = json.loads(await vision_analyze_tool(url, "describe", "test/model", region=[0, 0, 20, 10]))

    assert result["success"] is True, result
    assert mid_download == []
    part, files_while_provider_reads = seen[0]
    assert files_while_provider_reads == []
    raw = base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as sent:
        assert sent.format == "PNG" and sent.size == (20, 10)
    assert _image_files(roots) == []


@pytest.mark.asyncio
async def test_downloaded_video_reaches_the_provider_without_a_plaintext_file(roots):
    """An HTTP video is uploaded from memory: while the provider reads it, no file under the
    profile home or the temp dir holds its bytes."""
    from tools.vision_tools import video_analyze_tool
    url = "https://media.test/meeting.mp4"
    video = b"\x00\x00\x00\x18ftypmp42" + VIDEO_CANARY * 8
    seen: list = []
    with (
        _http_server(video, url),
        patch("tools.url_safety.async_is_safe_url", new_callable=AsyncMock, return_value=True),
        patch("tools.vision_tools.check_website_access", return_value=None),
        patch("tools.vision_tools.async_call_llm",
              side_effect=_provider(seen, lambda: _files(roots, lambda d: VIDEO_CANARY in d))),
        patch("tools.vision_tools.extract_content_or_reasoning", return_value="described"),
    ):
        result = json.loads(await video_analyze_tool(url, "what happens?"))

    assert result["success"] is True, result
    part, files_while_provider_reads = seen[0]
    assert files_while_provider_reads == []
    assert part["video_url"]["url"] == "data:video/mp4;base64," + base64.b64encode(video).decode("ascii")


@pytest.fixture
def svg_fallback(tmp_path, monkeypatch):
    """No in-memory rasterizer (cairosvg absent); a file-based one that records where it ran."""
    ram = tmp_path / "runtime"
    ram.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(ram))
    monkeypatch.setitem(sys.modules, "cairosvg", None)
    calls: list = []

    def rasterize(svg_path, out_path):
        calls.append((Path(svg_path), Path(svg_path).read_bytes()))
        Path(out_path).write_bytes(_png_bytes())
        return True
    monkeypatch.setattr("tools.vision_tools_image_prep._rasterize_svg_to_png", rasterize)
    return ram, calls


def _prove(monkeypatch, proven: bool):
    proof = RamProof(True, "ramfs", "synthetic unit proof") if proven else RamProof(
        False, "tmpfs", "synthetic: tmpfs pages can be written to active swap")
    monkeypatch.setattr("tools.browser_media_custody._prove", lambda _path: proof, raising=False)


@pytest.mark.asyncio
async def test_svg_file_rasterizer_without_proven_ram_fails_closed_with_guidance(roots, svg_fallback, monkeypatch):
    """With no proven RAM workspace the file-based rasterizer is never handed the SVG: the tool
    explains how to fix it, the request-path converter falls back to its placeholder, and no
    copy of the SVG is written anywhere."""
    from tools.vision_tools import vision_analyze_tool
    from tools.vision_tools_image_prep import rasterize_svg_data_url
    ram, calls = svg_fallback
    _prove(monkeypatch, proven=False)
    data_url = "data:image/svg+xml;base64," + base64.b64encode(SVG).decode("ascii")
    with patch("tools.vision_tools.async_call_llm", new_callable=AsyncMock) as provider:
        result = json.loads(await vision_analyze_tool(data_url, "describe", "test/model"))

    assert result["success"] is False
    assert "security.protected_work_dir" in result["error"] and "cairosvg" in result["error"]
    provider.assert_not_awaited()
    assert rasterize_svg_data_url(data_url) is None
    assert calls == []
    assert _files([*roots, ram], lambda d: SVG_CANARY in d) == []


@pytest.mark.asyncio
async def test_svg_file_rasterizer_runs_only_inside_the_proven_ram_workspace(roots, svg_fallback, monkeypatch):
    """With a proven RAM workspace the fallback rasterizes there; the model sees the PNG and the
    workspace is gone afterwards, leaving no SVG or PNG copy behind."""
    from tools.vision_tools import vision_analyze_tool
    ram, calls = svg_fallback
    _prove(monkeypatch, proven=True)
    data_url = "data:image/svg+xml;base64," + base64.b64encode(SVG).decode("ascii")
    seen: list = []
    with patch("tools.vision_tools.async_call_llm", side_effect=_provider(seen, lambda: None)):
        result = json.loads(await vision_analyze_tool(data_url, "describe", "test/model"))

    assert result["success"] is True, result
    [(svg_path, svg_bytes)] = calls
    assert svg_path.is_relative_to(ram) and svg_bytes == SVG
    assert seen[0][0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert list(ram.iterdir()) == []
    assert _image_files(roots) == [] and _files(roots, lambda d: SVG_CANARY in d) == []
