"""computer_use captures are retained only as encrypted profile artifacts.

A capture used to leave raw pixels in ``cache/images``/``image_cache``, raw untruncated labels and
window titles in ``cache/computer_use``/``computer_use_cache``, a staged copy in ``cache/vision`` for
auxiliary vision, and a ``cua_shot_*.png`` in the system temp dir for the CLI ``get_window_state``
fallback. These contracts pin the replacement: the shared authenticated artifact writer, the existing
typed readers (vision_analyze, read_file), bounded retention of this producer's copies only, a RAM-only
protected workspace for the driver's screenshot file, and explicit unavailability instead of plaintext.

Only external boundaries are faked: the cua-driver CLI process, the vision provider call, and the RAM
proof (the labelled unit seam ``tools.browser_media_custody._prove``, not evidence of a real mount; the
Linux-marked test uses the live host proof).
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hermes_platform.memory_protection import RamProof
from hermes_security import artifacts, vault
from tools.computer_use.backend import CaptureResult, UIElement

Image = pytest.importorskip("PIL.Image")
PngImagePlugin = pytest.importorskip("PIL.PngImagePlugin")

CANARY = "synthetic-private-screen-canary-4be1"
PIXEL = (201, 17, 133)


def _png() -> bytes:
    """A valid PNG carrying a plaintext tEXt canary, so ciphertext checks are meaningful."""
    out, meta = io.BytesIO(), PngImagePlugin.PngInfo()
    meta.add_text("private", CANARY)
    Image.new("RGB", (16, 12), PIXEL).save(out, "PNG", pnginfo=meta)
    return out.getvalue()


SHOT = _png()


def _label(index: int) -> str:
    return f"{CANARY} message {index} " + "x" * 300  # beyond the in-context label cap


def _capture(png: bytes | None = SHOT, count: int = 120) -> CaptureResult:
    """A dense private chat window: more elements and longer labels than one response carries."""
    elements = [UIElement(index=i, role="AXStaticText", label=_label(i), bounds=(i, 0, 10, 10), app="Chat")
                for i in range(count)]
    return CaptureResult(mode="som", width=16, height=12, png_b64=base64.b64encode(png).decode() if png else None,
                         image_mime_type="image/png" if png else None, elements=elements, app="Chat",
                         window_title="Private chat", png_bytes_len=len(png or b""))


def _plaintext(root: Path) -> list[Path]:
    return [p for p in Path(root).rglob("*") if p.is_file() and CANARY.encode() in p.read_bytes()]


def _pixels(data_url: str) -> set:
    with Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1]))) as image:
        return set(image.convert("RGB").getdata())


def _inline_image(resp: dict) -> bytes:
    return base64.b64decode(resp["content"][1]["image_url"]["url"].split(",", 1)[1])


@pytest.fixture
def home(tmp_path, monkeypatch):
    selected = tmp_path / "owner"
    selected.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(selected))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(selected, "synthetic computer_use custody password")
    vault.unlock(selected, "synthetic computer_use custody password")
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


@pytest.fixture
def native_route(monkeypatch):
    from tools.computer_use import tool as cu_tool
    monkeypatch.setattr(cu_tool, "_should_route_through_aux_vision", lambda: False)
    return cu_tool


def _aux_response(text: str):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = text
    return response


def test_capture_retains_encrypted_originals_reopened_by_existing_readers(home, native_route):
    from tools import file_tools, vision_tools

    resp = native_route._capture_response(_capture())
    assert resp["_multimodal"] is True and _inline_image(resp) == SHOT
    shot, tree = Path(resp["meta"]["screenshot_path"]), Path(resp["meta"]["elements_file"])
    assert shot.parent == home.resolve() / "artifacts" / "media" / "images"
    assert re.fullmatch(r"computer_use_[0-9a-f]{32}\.png", shot.name)
    assert tree.parent == home.resolve() / "artifacts" / "media" / "documents"
    assert re.fullmatch(r"elements_[0-9a-f]{32}\.json", tree.name)
    assert str(shot) in resp["text_summary"] and str(tree) in resp["text_summary"]
    assert _plaintext(home) == [], "pixels, labels and window titles must not persist as plaintext"

    assert artifacts.read_bytes(shot, home=home) == SHOT
    full = json.loads(artifacts.read_bytes(tree, home=home))
    assert full["total_elements"] == len(full["elements"]) == 120
    assert full["elements"][119]["label"] == _label(119) and full["window_title"] == "Private chat"
    with patch("tools.vision_tools._resolve_repeat_cap", return_value=0):
        native = asyncio.run(vision_tools._vision_analyze_native(str(shot), "what?"))
    assert _pixels(native["content"][1]["image_url"]["url"]) == {PIXEL}
    read = json.loads(file_tools.read_file_tool(str(tree), task_id="cua-private"))
    assert not read.get("error") and _label(0) in read["content"]
    found = json.loads(file_tools.search_tool("message 119", path=str(tree), task_id="cua-private-search"))
    assert not found.get("error") and found["matches"]
    assert any("message 119" in row["content"] for row in found["matches"])
    assert _plaintext(home) == []


def test_aux_vision_receives_capture_pixels_in_memory_without_staging(home, monkeypatch):
    from tools.computer_use import tool as cu_tool

    seen: list[str] = []

    async def provider(**kwargs):
        seen.append(kwargs["messages"][0]["content"][1]["image_url"]["url"])
        return _aux_response("a private chat window")
    monkeypatch.setattr(cu_tool, "_should_route_through_aux_vision", lambda: True)
    with patch("tools.vision_tools.async_call_llm", side_effect=provider):
        body = json.loads(cu_tool._capture_response(_capture()))
    assert "a private chat window" in body["vision_analysis"]
    assert len(seen) == 1 and _pixels(seen[0]) == {PIXEL}
    assert artifacts.read_bytes(body["screenshot_path"], home=home) == SHOT
    assert _plaintext(home) == []


@pytest.mark.parametrize("custody", ["locked", "uninitialized"])
def test_unavailable_vault_keeps_in_memory_evidence_and_never_claims_a_file(home, tmp_path, monkeypatch,
                                                                             native_route, custody):
    owner = home
    if custody == "locked":
        vault.lock_now(home)
    else:
        owner = tmp_path / "unvaulted"
        owner.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(owner))

    resp = native_route._capture_response(_capture())
    assert _inline_image(resp) == SHOT, "the capture's own evidence still reaches the model"
    assert "screenshot_path" not in resp["meta"] and "elements_file" not in resp["meta"]
    assert resp["meta"]["screenshot_unavailable"] and resp["meta"]["elements_file_unavailable"]
    assert "not retained" in resp["text_summary"] and "saved to" not in resp["text_summary"]
    text = json.loads(native_route._capture_response(_capture(png=None)))
    assert text["elements_file_unavailable"] and "elements_file" not in text
    assert "read_file" not in text["summary"] and len(text["elements"]) < text["total_elements"]
    assert _plaintext(owner) == [] and not (owner / "cache").exists()
    assert not [p for p in owner.joinpath("artifacts").rglob("*") if p.is_file()]


def test_retention_bounds_only_this_producers_encrypted_copies(home, monkeypatch, native_route):
    from tools import media_cache
    from tools.computer_use import private_media

    monkeypatch.setattr(private_media, "RETAINED_PER_FAMILY", 3)
    legacy = home / "cache" / "images" / f"computer_use_{'0' * 32}.png"
    legacy.parent.mkdir(parents=True, mode=0o700)
    legacy.write_bytes(SHOT)
    others = [Path(media_cache.cache_image_from_bytes(SHOT, ".png")),
              Path(media_cache.cache_document_from_bytes(b"{}", "mcp.json"))]
    metas = [native_route._capture_response(_capture())["meta"] for _ in range(5)]
    media = home.resolve() / "artifacts" / "media"
    assert len(list((media / "images").glob("computer_use_*"))) == 3
    assert len(list((media / "documents").glob("elements_*"))) == 3
    assert Path(metas[-1]["screenshot_path"]).exists() and Path(metas[-1]["elements_file"]).exists()
    assert all(p.exists() for p in others), "other producers' artifacts are never pruned"
    assert legacy.read_bytes() == SHOT, "legacy copies are left for explicit repair, not deleted"


def _driver_cli(monkeypatch, run):
    from tools.computer_use.cua_backend_session import _CuaDriverSession
    monkeypatch.setattr("tools.computer_use.cua_backend_driver.resolve_cua_driver_cmd", lambda: "/resolved/cua-driver")
    monkeypatch.setattr(subprocess, "run", run)
    return _CuaDriverSession.__new__(_CuaDriverSession)


def _daemon(targets: list):
    """Fake cua-driver CLI: writes the screenshot to the requested file and answers with a tiny body."""
    def run(cmd, **_kw):
        target = Path(json.loads(cmd[3])["screenshot_out_file"])
        targets.append((target, stat.S_IMODE(target.parent.stat().st_mode)))
        target.write_bytes(SHOT)
        body = {"element_count": 1, "tree_markdown": "- [0] AXButton", "screenshot_file_path": str(target)}
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(body))
    return run


def test_cli_window_state_file_lives_only_in_protected_ram_and_is_removed(home, ram, monkeypatch):
    targets: list = []
    session = _driver_cli(monkeypatch, _daemon(targets))
    out = session._call_tool_via_cli("get_window_state", {"pid": 1, "window_id": 2}, 30.0)
    assert out["images"] == [base64.b64encode(SHOT).decode("ascii")]
    assert "AXButton" in out["data"] and "1 elements" in out["data"]
    assert targets[0][0].is_relative_to(ram) and targets[0][1] == 0o700
    assert list(ram.iterdir()) == []

    def crashed(cmd, **_kw):
        Path(json.loads(cmd[3])["screenshot_out_file"]).write_bytes(SHOT)
        raise OSError("synthetic driver crash")
    monkeypatch.setattr(subprocess, "run", crashed)
    with pytest.raises(RuntimeError, match="failed to spawn"):
        session._call_tool_via_cli("get_window_state", {"pid": 1, "window_id": 2}, 30.0)
    assert list(ram.iterdir()) == [] and _plaintext(home) == []


def test_cli_window_state_refuses_without_proven_ram_and_spawns_nothing(home, ram, monkeypatch):
    monkeypatch.setattr("tools.browser_media_custody._prove",
                        lambda _path: RamProof(False, "tmpfs", "synthetic: active swap"))
    spawned: list = []
    session = _driver_cli(monkeypatch, lambda cmd, **_kw: spawned.append(json.loads(cmd[3])) or SimpleNamespace(
        returncode=0, stderr="", stdout='{"windows": []}'))
    with pytest.raises(RuntimeError, match="protected_work_dir") as refused:
        session._call_tool_via_cli("get_window_state", {"pid": 1, "window_id": 2}, 30.0)
    assert "active swap" in str(refused.value) and spawned == []
    session._call_tool_via_cli("list_windows", {}, 10.0)  # other CLI calls are unchanged
    assert spawned == [{}]


@pytest.mark.platforms("posix")
@pytest.mark.require_symlinks
@pytest.mark.parametrize("replacement", ["reported-outside", "symlink", "hardlink"])
def test_cli_capture_never_reads_a_driver_selected_outside_file(home, ram, tmp_path, monkeypatch, replacement):
    outside = tmp_path / "outside.png"
    outside.write_bytes(SHOT)
    before = outside.read_bytes()

    def run(cmd, **_kw):
        target = Path(json.loads(cmd[3])["screenshot_out_file"])
        reported = str(target)
        if replacement == "reported-outside":
            reported = str(outside)
        else:
            target.unlink()
            if replacement == "symlink":
                target.symlink_to(outside)
            else:
                os.link(outside, target)
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({"screenshot_file_path": reported}))

    session = _driver_cli(monkeypatch, run)
    with pytest.raises((RuntimeError, OSError)):
        session._call_tool_via_cli("get_window_state", {"pid": 1, "window_id": 2,
                                                       "screenshot_out_file": str(outside)}, 30.0)
    assert outside.read_bytes() == before
    assert list(ram.iterdir()) == []


@pytest.mark.platforms("linux")
def test_native_ram_proof_hosts_the_cli_window_state_file(home, native_ram, monkeypatch):
    targets: list = []
    session = _driver_cli(monkeypatch, _daemon(targets))
    out = session._call_tool_via_cli("get_window_state", {"pid": 1, "window_id": 2}, 30.0)
    assert out["images"] == [base64.b64encode(SHOT).decode("ascii")]
    assert targets[0][0].is_relative_to(native_ram) and list(native_ram.iterdir()) == []


def test_legacy_copies_are_admitted_by_exact_producer_name_only(home):
    from hermes_security.migrate import repair_clobbered_state, scan_for_plaintext
    from tools import file_tools
    from tools.image_source import ResolveContext, resolve_image_source

    def legacy(*parts: str, data: bytes) -> Path:
        path = home.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(data)
        path.chmod(0o600)
        return path
    tree = json.dumps({"elements": [{"label": _label(7)}]}).encode()
    # cache/* and image_cache were excluded from the old migration: these copies are still plaintext.
    shot = legacy("cache", "images", f"computer_use_{'a' * 32}.png", data=SHOT)
    spilled = legacy("cache", "computer_use", f"elements_{'b' * 32}.json", data=tree)
    neighbour = legacy("cache", "images", f"clipboard_{'c' * 32}.png", data=SHOT)  # another producer
    # computer_use_cache was migrated with the generic state envelope bound to its relative path.
    migrated = home / "computer_use_cache" / f"elements_{'d' * 32}.json"
    migrated.parent.mkdir(mode=0o700)
    vault.get_vault(home).write_bytes(migrated, tree, purpose="state")

    pending = set(scan_for_plaintext(home))
    assert {shot.relative_to(home).as_posix(), spilled.relative_to(home).as_posix()} <= pending
    assert not {neighbour.relative_to(home).as_posix(), migrated.relative_to(home).as_posix()} & pending
    assert repair_clobbered_state(home)["unrecoverable"] == []
    assert scan_for_plaintext(home) == []
    resolved = asyncio.run(resolve_image_source(str(shot), ResolveContext()))
    assert resolved.data == SHOT and resolved.origin == "artifact"
    for path in (spilled, migrated):
        read = json.loads(file_tools.read_file_tool(str(path), task_id="cua-legacy"))
        assert not read.get("error") and _label(7) in read["content"]
    assert _plaintext(home) == [neighbour], "only this producer's exact names enter private policy"
