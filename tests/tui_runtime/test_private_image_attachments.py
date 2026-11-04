"""Owned TUI image uploads are sealed artifacts of the session's exact owning profile.

``image.attach_bytes`` and ``clipboard.paste`` used to write raw pixels under
``<profile home>/images``. They now seal into the profile's encrypted
``artifacts/media/images`` family, while the existing consumers (attach preview
metadata, native vision, resume projection) still see the exact original bytes, in
memory only. Only external boundaries are replaced: the host clipboard helper and
the labelled RAM-proof seam ``tools.browser_media_custody._prove``.
"""
from __future__ import annotations

import base64
import struct
import sys
import threading
import types
import zlib
from pathlib import Path

import pytest

from hermes_platform.memory_protection import RamProof
from hermes_security import artifacts, vault
from tui_runtime import server

CANARY = b"synthetic-tui-attachment-canary-41c9"


def _png(width: int, height: int, tag: bytes) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    rows = b"".join(b"\x00" + b"\x10\x80\xf0" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"tEXt", b"Comment\x00" + CANARY + tag) + chunk(b"IDAT", zlib.compress(rows))
            + chunk(b"IEND", b""))


UPLOAD = _png(7, 3, b"-upload")
CLIP = _png(5, 9, b"-clip")


def _vault_home(path: Path, *, unlock: bool = True) -> Path:
    path.mkdir(parents=True)
    vault.init_vault(path, "synthetic attachment custody password")
    if unlock:
        vault.unlock(path, "synthetic attachment custody password")
    return path


def _plaintext_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and CANARY in p.read_bytes()]


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """A launch home and a distinct profile owner; attach runs with the LAUNCH home ambient."""
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    monkeypatch.delenv("HERMES_VAULT_PRIVATE_KEY", raising=False)
    launch = _vault_home(tmp_path / "launch")
    owner = _vault_home(tmp_path / "launch" / "profiles" / "owner")
    monkeypatch.setenv("HERMES_HOME", str(launch))
    monkeypatch.setattr(server, "_hermes_home", launch)
    fake_cli = types.ModuleType("cli")
    fake_cli._IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
    monkeypatch.setitem(sys.modules, "cli", fake_cli)
    monkeypatch.setattr(server, "_start_agent_build", lambda sid, session: None)
    yield launch, owner
    vault.clear_vault_cache()


@pytest.fixture
def ram(tmp_path, monkeypatch):
    """Unit prover seam: a tmp directory declared RAM-only. Not evidence of a real mount."""
    base = tmp_path / "ram"
    base.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(base))
    monkeypatch.setattr("tools.browser_media_custody._prove",
                        lambda _path: RamProof(True, "ramfs", "synthetic unit proof"))
    return base


@pytest.fixture
def clipboard(monkeypatch):
    """The host clipboard helper: writes CLIP to whatever target the runtime hands it."""
    targets: list[Path] = []

    def save(target):
        targets.append(Path(target))
        Path(target).write_bytes(CLIP)
        return True
    monkeypatch.setattr("hermes_cli.clipboard.save_clipboard_image", save)
    monkeypatch.setattr("hermes_cli.clipboard.has_clipboard_image", lambda: True)
    return targets


def _session(sid: str, profile_home: Path, tmp_path: Path) -> dict:
    record = {
        "agent": None, "agent_ready": threading.Event(), "agent_error": None, "attached_images": [],
        "cwd": str(tmp_path), "history": [], "history_lock": threading.RLock(), "history_version": 0,
        "image_counter": 0, "profile_home": str(profile_home), "running": False, "session_key": sid,
        "transport": None,
    }
    server._sessions[sid] = record
    return record


def _rpc(method: str, params: dict) -> dict:
    return server.handle_request({"id": "1", "method": method, "params": params})


def _attach_both(sid: str) -> tuple[dict, dict]:
    upload = _rpc("image.attach_bytes", {"session_id": sid, "filename": "shot.png",
                                         "content_base64": base64.b64encode(UPLOAD).decode()})
    clip = _rpc("clipboard.paste", {"session_id": sid})
    return upload, clip


def test_owned_uploads_seal_for_the_exact_owner_and_round_trip_to_every_consumer(
        homes, ram, clipboard, tmp_path, monkeypatch):
    launch, owner = homes
    record = _session("owned-images", owner, tmp_path)
    try:
        upload, clip = _attach_both("owned-images")
    finally:
        server._sessions.pop("owned-images", None)

    paths = []
    for response, original, size in ((upload, UPLOAD, (7, 3)), (clip, CLIP, (5, 9))):
        result = response["result"]
        assert result["attached"] is True and (result["width"], result["height"]) == size
        sealed = Path(result["path"])
        assert sealed.parent == owner.resolve() / "artifacts" / "media" / "images"
        assert artifacts.read_bytes(sealed, home=owner) == original
        paths.append(str(sealed))
    assert record["attached_images"] == paths
    assert clipboard and all(target.is_relative_to(ram) for target in clipboard)
    assert list(ram.iterdir()) == [], "the RAM capture workspace must be removed"
    assert _plaintext_files(tmp_path) == []
    assert not (owner / "images").exists() and not (launch / "images").exists()

    # Native vision inside the owner's turn scope receives the exact pixels, decrypted in memory.
    monkeypatch.setenv("HERMES_HOME", str(owner))
    from agent.image_routing import build_native_content_parts
    parts, skipped = build_native_content_parts("Describe", paths)
    assert skipped == []
    embedded = [base64.b64decode(p["image_url"]["url"].split(",", 1)[1]) for p in parts[1:]]
    assert embedded == [UPLOAD, CLIP]

    # Resume runs outside the turn scope: the explicit owner verifies the stored native row.
    monkeypatch.setenv("HERMES_HOME", str(launch))
    stored = server._build_persist_user_message("Describe", paths, parts)
    shown = server._history_to_messages([{"role": "user", "content": stored}], profile_home=str(owner))
    assert shown == [{"role": "user", "text": stored[0]["text"]}]
    assert _plaintext_files(tmp_path) == []


@pytest.mark.parametrize("custody", ["missing", "locked"])
def test_owner_without_live_custody_fails_closed_and_foreign_artifacts_are_refused(
        homes, ram, clipboard, tmp_path, monkeypatch, custody):
    launch, owner = homes
    blocked = tmp_path / "blocked"
    if custody == "missing":
        blocked.mkdir()
    else:
        _vault_home(blocked, unlock=False)
    record = _session("closed-owner", blocked, tmp_path)
    try:
        upload, clip = _attach_both("closed-owner")
    finally:
        server._sessions.pop("closed-owner", None)
    assert upload["error"]["code"] == 5027 and clip["error"]["code"] == 5027
    assert record["attached_images"] == [] and record["image_counter"] == 0
    assert _plaintext_files(tmp_path) == []
    assert not (blocked / "images").exists() and not (blocked / "artifacts").exists()
    assert not (launch / "artifacts" / "media").exists(), "no ambient launch-home substitute"

    # Another profile's sealed upload is never read with this profile's custody.
    sealed = server.stage_image_bytes(owner, UPLOAD, ".png", prefix="upload")
    monkeypatch.setenv("HERMES_HOME", str(launch))
    from agent.image_routing import build_native_content_parts
    assert build_native_content_parts("Describe", [str(sealed)])[1] == [str(sealed)]
    url = "data:image/png;base64," + base64.b64encode(UPLOAD).decode()
    row = {"role": "user", "content": [{"type": "text", "text": f"Describe\n@image:{sealed}"},
                                       {"type": "image_url", "image_url": {"url": url}}]}
    assert url not in server._history_to_messages([row], profile_home=str(owner))[0]["text"]
    assert url in server._history_to_messages([row], profile_home=str(launch))[0]["text"]
