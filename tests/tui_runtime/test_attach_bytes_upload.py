"""Remote-client byte uploads: ``image.attach_bytes`` and ``pdf.attach`` validation and staging.

Moved from ``test_tui_runtime_server.py``; uploads are sealed into the owner's encrypted
``artifacts/media/images`` (see ``test_private_image_attachments.py`` for custody contracts).
"""
import sys
import threading
import types
from pathlib import Path

from tui_runtime import server


def _session(**extra):
    return {
        "agent": types.SimpleNamespace(), "session_key": "session-key", "history": [],
        "history_lock": threading.Lock(), "history_version": 0, "running": False,
        "attached_images": [], "image_counter": 0, "cols": 80, **extra,
    }


# Smallest valid 1x1 PNG, base64-encoded.
_PNG_1X1_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _attach_bytes_cli(monkeypatch):
    fake_cli = types.ModuleType("cli")
    fake_cli._IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
    monkeypatch.setitem(sys.modules, "cli", fake_cli)


def _sealed_launch_home(monkeypatch, tmp_path):
    """The launch home as an initialized, unlocked owner: uploads are sealed artifacts."""
    from hermes_security import vault

    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(tmp_path, "synthetic upload password")
    vault.unlock(tmp_path, "synthetic upload password")
    monkeypatch.setattr(server, "_hermes_home", tmp_path)


def test_image_attach_bytes_writes_to_gateway_dir(monkeypatch, tmp_path):
    """Remote client uploads base64 bytes; the owner's encrypted image artifacts receive them."""
    import base64

    from hermes_security import artifacts

    _attach_bytes_cli(monkeypatch)
    _sealed_launch_home(monkeypatch, tmp_path)
    server._sessions["abx"] = _session()

    resp = server.handle_request(
        {
            "id": "1",
            "method": "image.attach_bytes",
            "params": {
                "session_id": "abx",
                "content_base64": _PNG_1X1_B64,
                "filename": "shot.png",
            },
        }
    )

    res = resp["result"]
    assert res["attached"] is True
    written = Path(res["path"])
    assert written.parent == tmp_path.resolve() / "artifacts" / "media" / "images"
    assert not written.read_bytes().startswith(b"\x89PNG")
    assert artifacts.read_bytes(written, home=tmp_path) == base64.b64decode(_PNG_1X1_B64)
    assert len(server._sessions["abx"]["attached_images"]) == 1
    assert res["bytes"] > 0


def test_image_attach_bytes_accepts_data_url_prefix(monkeypatch, tmp_path):
    _attach_bytes_cli(monkeypatch)
    _sealed_launch_home(monkeypatch, tmp_path)
    server._sessions["abx2"] = _session()

    resp = server.handle_request(
        {
            "id": "1",
            "method": "image.attach_bytes",
            "params": {
                "session_id": "abx2",
                "content_base64": f"data:image/png;base64,{_PNG_1X1_B64}",
            },
        }
    )
    assert resp["result"]["attached"] is True


def test_image_attach_bytes_data_alias_and_magic_sniff(monkeypatch, tmp_path):
    """Older desktop builds send `data` (not content_base64); ext sniffed from bytes."""
    _attach_bytes_cli(monkeypatch)
    _sealed_launch_home(monkeypatch, tmp_path)
    server._sessions["abx3"] = _session()

    resp = server.handle_request(
        {
            "id": "1",
            "method": "image.attach_bytes",
            "params": {"session_id": "abx3", "data": _PNG_1X1_B64},
        }
    )
    res = resp["result"]
    assert res["attached"] is True
    assert Path(res["path"]).suffix == ".png"  # sniffed from magic bytes


def test_image_attach_bytes_rejects_invalid_base64(monkeypatch, tmp_path):
    _attach_bytes_cli(monkeypatch)
    monkeypatch.setattr(server, "_hermes_home", tmp_path)
    server._sessions["abx4"] = _session()

    resp = server.handle_request(
        {
            "id": "1",
            "method": "image.attach_bytes",
            "params": {"session_id": "abx4", "content_base64": "!!!not base64!!!"},
        }
    )
    assert "error" in resp
    assert resp["error"]["code"] == 4017


def test_image_attach_bytes_rejects_oversize(monkeypatch, tmp_path):
    import base64 as _b64

    _attach_bytes_cli(monkeypatch)
    monkeypatch.setattr(server, "_hermes_home", tmp_path)
    monkeypatch.setattr(server, "_ATTACH_BYTES_MAX_BYTES", 10)
    server._sessions["abx5"] = _session()

    big = _b64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 100).decode("ascii")
    resp = server.handle_request(
        {
            "id": "1",
            "method": "image.attach_bytes",
            "params": {"session_id": "abx5", "content_base64": big},
        }
    )
    assert "error" in resp
    assert resp["error"]["code"] == 4018


def test_image_attach_bytes_rejects_unsupported_extension(monkeypatch, tmp_path):
    _attach_bytes_cli(monkeypatch)
    monkeypatch.setattr(server, "_hermes_home", tmp_path)
    server._sessions["abx6"] = _session()

    # filename hint forces a non-image extension; magic sniff is bypassed by hint
    resp = server.handle_request(
        {
            "id": "1",
            "method": "image.attach_bytes",
            "params": {
                "session_id": "abx6",
                "content_base64": _PNG_1X1_B64,
                "filename": "evil.exe",
            },
        }
    )
    assert "error" in resp
    assert resp["error"]["code"] == 4016


def test_pdf_attach_requires_poppler(monkeypatch, tmp_path):
    """Without pdftoppm on PATH, pdf.attach returns a clear 5028."""
    _attach_bytes_cli(monkeypatch)
    monkeypatch.setattr(server, "_hermes_home", tmp_path)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    server._sessions["pdf1"] = _session()

    resp = server.handle_request(
        {
            "id": "1",
            "method": "pdf.attach",
            "params": {"session_id": "pdf1", "content_base64": "JVBERi0xLjQK"},
        }
    )
    assert "error" in resp
    assert resp["error"]["code"] == 5028


def test_pdf_attach_rejects_non_pdf_bytes(monkeypatch, tmp_path):
    import base64 as _b64
    from hermes_platform.memory_protection import RamProof

    _attach_bytes_cli(monkeypatch)
    monkeypatch.setattr(server, "_hermes_home", tmp_path)
    (tmp_path / "ram").mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "ram"))
    monkeypatch.setattr("tools.browser_media_custody._prove",
                        lambda _path: RamProof(True, "ramfs", "synthetic unit proof"))
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/pdftoppm")
    server._sessions["pdf2"] = _session()

    not_pdf = _b64.b64encode(b"this is not a pdf").decode("ascii")
    resp = server.handle_request(
        {
            "id": "1",
            "method": "pdf.attach",
            "params": {"session_id": "pdf2", "content_base64": not_pdf},
        }
    )
    assert "error" in resp
    assert resp["error"]["code"] == 4017


def test_pdf_attach_requires_path_or_bytes(monkeypatch, tmp_path):
    _attach_bytes_cli(monkeypatch)
    monkeypatch.setattr(server, "_hermes_home", tmp_path)
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/pdftoppm")
    server._sessions["pdf3"] = _session()

    resp = server.handle_request(
        {"id": "1", "method": "pdf.attach", "params": {"session_id": "pdf3"}}
    )
    assert "error" in resp
    assert resp["error"]["code"] == 4015


def test_decode_attach_base64_helper():
    import base64 as _b64

    raw = _b64.b64encode(b"hello").decode("ascii")
    assert server._decode_attach_base64(raw, mime_prefix="image/") == b"hello"
    assert (
        server._decode_attach_base64(f"data:image/png;base64,{raw}", mime_prefix="image/")
        == b"hello"
    )
    # whitespace inside payload is tolerated
    assert server._decode_attach_base64(raw[:4] + "\n" + raw[4:], mime_prefix="image/") == b"hello"
    assert server._decode_attach_base64("@@@", mime_prefix="image/") is None


def test_sniff_image_ext_magic_and_filename():
    assert server._sniff_image_ext(b"\x89PNG\r\n\x1a\n") == ".png"
    assert server._sniff_image_ext(b"\xff\xd8\xff\xe0") == ".jpg"
    assert server._sniff_image_ext(b"GIF89a....") == ".gif"
    assert server._sniff_image_ext(b"RIFF1234WEBPxxxx") == ".webp"
    assert server._sniff_image_ext(b"BM......") == ".bmp"
    assert server._sniff_image_ext(b"unknown") == ".png"  # fallback
    # filename hint wins over magic bytes
    assert server._sniff_image_ext(b"\x89PNG", "photo.jpeg") == ".jpeg"
