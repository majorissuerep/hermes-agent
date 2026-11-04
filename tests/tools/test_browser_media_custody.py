"""Browser screenshots are RAM-staged, sealed as owned artifacts, and still analysable.

Only the external boundaries are faked: the agent-browser/Lightpanda command, the Camofox
HTTP response, and the vision provider calls. The RAM proof uses the labelled unit seam
``tools.browser_media_custody._prove`` except in the native Linux test, which needs a real
provable mount and skips honestly without one.
"""
from __future__ import annotations

import base64
import io
import json
import os
import struct
import tempfile
import zlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes_platform.memory_protection import RamProof
from hermes_security import artifacts, vault

CANARY = b"synthetic-private-page-canary-7f3a"
PIXEL = (201, 17, 133)


def _png(width: int = 6, height: int = 4) -> bytes:
    """A valid RGB PNG with a plaintext tEXt canary, so ciphertext checks are meaningful."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    rows = b"".join(b"\x00" + bytes(PIXEL) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"tEXt", b"Comment\x00" + CANARY) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


SHOT = _png()


def _pixels(data_url: str) -> set:
    from PIL import Image
    raw = base64.b64decode(data_url.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as image:
        return set(image.convert("RGB").getdata())


def _plaintext_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and CANARY in p.read_bytes()]


@pytest.fixture
def home(tmp_path, monkeypatch):
    selected = tmp_path / "owner"
    selected.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(selected))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(selected, "synthetic browser custody password")
    vault.unlock(selected, "synthetic browser custody password")
    yield selected
    vault.clear_vault_cache()


@pytest.fixture
def ram(tmp_path, monkeypatch):
    """Unit prover seam: a tmp directory declared RAM-only. Not evidence of a real mount."""
    base = tmp_path / "ram"
    base.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(base))
    monkeypatch.setattr("tools.browser_media_custody._prove",
                        lambda _path: RamProof(True, "ramfs", "synthetic unit proof"), raising=False)
    return base


@pytest.fixture
def browser(ram):
    """Fake agent-browser: writes the canary PNG to the requested target and records it."""
    seen: list[Path] = []

    def run(_task, command, args, *_a, **_kw):
        if command != "screenshot":
            return {"success": False, "error": f"unexpected {command}"}
        target = Path(args[-1])
        seen.append(target)
        target.write_bytes(SHOT)
        return {"success": True, "data": {"path": str(target)}}
    with patch("tools.browser_tool_session._run_browser_command", side_effect=run), \
            patch("tools.browser_tool._is_camofox_mode", return_value=False), \
            patch("tools.browser_tool_cloud._get_browser_engine", return_value="auto"):
        yield seen


def _aux_response(text: str):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = text
    return response


def _assert_sealed(home: Path, path: str) -> Path:
    sealed = Path(path)
    assert sealed.is_relative_to(home / "artifacts" / "browser")
    stored = sealed.read_bytes()
    assert CANARY not in stored and not stored.startswith(b"\x89PNG")
    assert artifacts.read_bytes(sealed, home=home) == SHOT
    return sealed


def _assert_no_plaintext(home: Path, ram: Path) -> None:
    assert _plaintext_files(home) == []
    assert list(ram.iterdir()) == [], "the RAM workspace must be removed"
    assert not (home / "cache" / "screenshots").exists()
    assert not (home / "browser_screenshots").exists()


def test_native_browser_vision_embeds_ram_pixels_and_publishes_only_ciphertext(home, ram, browser):
    from tools import browser_tool
    with patch("tools.vision_tools._should_use_native_vision_fast_path", return_value=True):
        result = browser_tool.browser_vision("what is shown?", task_id="custody-native")
    assert result["_multimodal"] is True
    assert browser and browser[0].is_relative_to(ram)
    assert _pixels(result["content"][1]["image_url"]["url"]) == {PIXEL}
    sealed = _assert_sealed(home, result["meta"]["screenshot_path"])
    assert str(sealed) in result["text_summary"]
    _assert_no_plaintext(home, ram)


def test_aux_browser_vision_sends_pixels_and_keeps_encrypted_evidence_on_failure(home, ram, browser):
    from tools import browser_tool
    with patch("tools.vision_tools._should_use_native_vision_fast_path", return_value=False), \
            patch("agent.auxiliary_client.call_llm", return_value=_aux_response("a magenta page")) as llm:
        ok = json.loads(browser_tool.browser_vision("what is shown?", task_id="custody-aux"))
    assert ok["success"] is True and ok["analysis"] == "a magenta page"
    sent = llm.call_args.kwargs["messages"][0]["content"][1]["image_url"]["url"]
    assert base64.b64decode(sent.split(",", 1)[1]) == SHOT
    _assert_sealed(home, ok["screenshot_path"])

    with patch("tools.vision_tools._should_use_native_vision_fast_path", return_value=False), \
            patch("agent.auxiliary_client.call_llm", side_effect=RuntimeError("synthetic provider outage")):
        failed = json.loads(browser_tool.browser_vision("again?", task_id="custody-aux"))
    assert failed["success"] is False and "synthetic provider outage" in failed["error"]
    _assert_sealed(home, failed["screenshot_path"])
    _assert_no_plaintext(home, ram)


def test_lightpanda_preroute_captures_into_ram_and_moves_a_foreign_capture(home, ram, browser, tmp_path):
    from tools import browser_tool
    default_location = tmp_path / "agent-browser-default" / "shot.png"
    requested: list[Path] = []

    def chrome_fallback(_task, args, _timeout):
        requested.append(Path(args[-1]))
        default_location.parent.mkdir()
        default_location.write_bytes(SHOT)  # agent-browser chose its own location
        return {"success": True, "data": {"path": str(default_location)}}
    with patch("tools.browser_tool_cloud._get_browser_engine", return_value="lightpanda"), \
            patch("tools.browser_tool_cloud._should_inject_engine", return_value=True), \
            patch("tools.browser_tool_lightpanda_fallback._chrome_fallback_screenshot", side_effect=chrome_fallback), \
            patch("tools.vision_tools._should_use_native_vision_fast_path", return_value=True):
        result = browser_tool.browser_vision("what is shown?", task_id="custody-lp")
    assert requested and requested[0].is_relative_to(ram)
    assert not default_location.exists()
    assert _pixels(result["content"][1]["image_url"]["url"]) == {PIXEL}
    assert result["meta"]["fallback_warning"]
    _assert_sealed(home, result["meta"]["screenshot_path"])
    assert browser == [], "a successful pre-route must not take a second capture"
    _assert_no_plaintext(home, ram)


def test_unproven_ram_refuses_before_capture_with_an_actionable_error(home, ram, browser, monkeypatch):
    from tools import browser_tool
    monkeypatch.setattr("tools.browser_media_custody._prove",
                        lambda _path: RamProof(False, "tmpfs", "synthetic: active swap"))
    result = json.loads(browser_tool.browser_vision("what is shown?", task_id="custody-noram"))
    assert result["success"] is False
    assert "protected_work_dir" in result["error"] and "active swap" in result["error"]
    assert browser == []
    _assert_no_plaintext(home, ram)


def test_locked_vault_discards_the_capture_without_plaintext_or_artifacts(home, ram, browser):
    from tools import browser_tool
    vault.lock_now(home)
    with patch("tools.vision_tools._should_use_native_vision_fast_path", return_value=True):
        result = json.loads(browser_tool.browser_vision("what is shown?", task_id="custody-locked"))
    assert result["success"] is False and "encrypted" in result["error"]
    assert browser, "the capture was taken, then discarded"
    assert not (home / "artifacts").exists() or not any((home / "artifacts").rglob("*.png"))
    _assert_no_plaintext(home, ram)


def test_camofox_seals_response_bytes_and_analyses_them_in_memory(home, ram, monkeypatch):
    from tools import browser_camofox
    monkeypatch.setenv("CAMOFOX_URL", "http://localhost:9377")
    raw = MagicMock()
    raw.content = SHOT
    with patch("tools.browser_camofox.requests.post") as post, \
            patch("tools.browser_camofox._get_raw", return_value=raw), \
            patch("agent.auxiliary_client.call_llm", return_value=_aux_response("camofox page")) as llm:
        post.return_value = MagicMock(status_code=200, json=lambda: {"tabId": "tab-c", "url": "https://x.test"})
        post.return_value.raise_for_status.return_value = None
        browser_camofox.camofox_navigate("https://x.test", task_id="custody-camofox")
        result = json.loads(browser_camofox.camofox_vision("what?", task_id="custody-camofox"))
    assert result["success"] is True and result["analysis"] == "camofox page"
    sent = llm.call_args.kwargs["messages"][0]["content"][1]["image_url"]["url"]
    assert base64.b64decode(sent.split(",", 1)[1]) == SHOT
    _assert_sealed(home, result["screenshot_path"])
    _assert_no_plaintext(home, ram)


def _seal_shot(home: Path) -> Path:
    from tools.browser_media_custody import seal_camofox_capture
    return seal_camofox_capture(SHOT, home)


@pytest.mark.parametrize("via_home_alias", [False, True])
def test_returned_encrypted_path_reopens_for_native_and_aux_analysis(home, ram, tmp_path, monkeypatch,
                                                                    via_home_alias):
    import asyncio

    from tools import vision_tools
    sealed = _seal_shot(home)
    if via_home_alias:
        alias = tmp_path / "configured-home"
        alias.symlink_to(home, target_is_directory=True)
        monkeypatch.setenv("HERMES_HOME", str(alias))
        sealed = alias / sealed.relative_to(home)
    with patch("tools.vision_tools._resolve_repeat_cap", return_value=0):
        native = asyncio.run(vision_tools._vision_analyze_native(str(sealed), "what?"))
    assert native["_multimodal"] is True
    assert _pixels(native["content"][1]["image_url"]["url"]) == {PIXEL}

    seen: list[str] = []

    async def provider(**kwargs):
        seen.append(kwargs["messages"][0]["content"][1]["image_url"]["url"])
        return _aux_response("reopened magenta page")
    with patch("tools.vision_tools.async_call_llm", side_effect=provider):
        aux = json.loads(asyncio.run(vision_tools.vision_analyze_tool(str(sealed), "describe")))
    assert aux["success"] is True and "reopened magenta page" in aux["analysis"]
    assert _pixels(seen[0]) == {PIXEL}
    _assert_no_plaintext(home, ram)


def test_damaged_or_foreign_artifacts_are_refused_without_mutation(home, ram, tmp_path):
    import asyncio

    from tools.image_source import ResolveContext, SourceUnsafe, resolve_image_source
    sealed = _seal_shot(home)
    damaged = bytearray(sealed.read_bytes())
    damaged[-1] ^= 0xFF
    sealed.write_bytes(bytes(damaged))
    with pytest.raises(SourceUnsafe):
        asyncio.run(resolve_image_source(str(sealed), ResolveContext()))
    assert sealed.read_bytes() == bytes(damaged)

    foreign = tmp_path / "other-profile"
    (foreign / "artifacts" / "browser").mkdir(parents=True)
    (foreign / ".hermes-vault").write_text("{}")
    planted = foreign / "artifacts" / "browser" / "x.png"
    planted.write_bytes(SHOT)
    with pytest.raises(SourceUnsafe):
        asyncio.run(resolve_image_source(str(planted), ResolveContext()))
    _assert_no_plaintext(home, ram)


def test_ordinary_images_need_neither_ram_proof_nor_the_vault(home, ram, tmp_path, monkeypatch):
    import asyncio

    from tools.image_source import ResolveContext, resolve_image_source
    monkeypatch.setattr("tools.browser_media_custody._prove",
                        lambda _path: RamProof(False, "tmpfs", "synthetic: active swap"))
    vault.lock_now(home)
    public = tmp_path / "public.png"
    public.write_bytes(SHOT)
    resolved = asyncio.run(resolve_image_source(str(public), ResolveContext()))
    assert resolved.data == SHOT and resolved.origin == "file"


@pytest.fixture
def native_ram(monkeypatch):
    """A real writable mount the live host proof accepts as RAM-only; skip otherwise."""
    from hermes_security.protected_workspace import protected_workspace_status
    with open("/proc/self/mountinfo", encoding="utf-8") as handle:
        mounts = [line.split(" - ", 1) for line in handle if " - " in line]
    for head, tail in mounts:
        point = head.split()[4].replace("\\040", " ")
        if tail.split()[0] in {"ramfs", "tmpfs"} and os.access(point, os.W_OK | os.X_OK):
            base = Path(tempfile.mkdtemp(dir=point, prefix="hermes-test-runtime-"))
            if protected_workspace_status(base).proven:
                monkeypatch.setenv("XDG_RUNTIME_DIR", str(base))
                yield base
                os.rmdir(base)
                return
            os.rmdir(base)
    pytest.skip("this host has no writable RAM-only mount that is provably never swapped")


@pytest.mark.platforms("linux")
def test_native_ram_proof_capture_round_trip(home, native_ram):
    from tools import browser_tool
    seen: list[Path] = []

    def run(_task, _command, args, *_a, **_kw):
        seen.append(Path(args[-1]))
        Path(args[-1]).write_bytes(SHOT)
        return {"success": True, "data": {"path": args[-1]}}
    with patch("tools.browser_tool_session._run_browser_command", side_effect=run), \
            patch("tools.browser_tool._is_camofox_mode", return_value=False), \
            patch("tools.browser_tool_cloud._get_browser_engine", return_value="auto"), \
            patch("tools.vision_tools._should_use_native_vision_fast_path", return_value=True):
        result = browser_tool.browser_vision("what?", task_id="custody-native-ram")
    assert seen and seen[0].is_relative_to(native_ram)
    assert _pixels(result["content"][1]["image_url"]["url"]) == {PIXEL}
    _assert_sealed(home, result["meta"]["screenshot_path"])
    _assert_no_plaintext(home, native_ram)
