"""MCP media retains authenticated original bytes and usable existing consumers."""
from __future__ import annotations

import base64
from io import BytesIO
import json
from pathlib import Path
import secrets
from types import SimpleNamespace
import zipfile

from PIL import Image, PngImagePlugin
import pytest

from hermes_security import artifacts, vault
from hermes_security.errors import VaultIntegrityError
from tools import media_cache


@pytest.fixture
def home(tmp_path, monkeypatch):
    selected = tmp_path / "owner"
    selected.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(selected))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(selected, "synthetic media custody password")
    vault.unlock(selected, "synthetic media custody password")
    yield selected
    vault.clear_vault_cache()


def _image(canary):
    output = BytesIO()
    meta = PngImagePlugin.PngInfo()
    meta.add_text("private", canary)
    Image.new("RGB", (12, 8), "red").save(output, format="PNG", pnginfo=meta)
    return output.getvalue()


def test_media_originals_are_encrypted_recoverable_and_not_interchangeable(home):
    canary = secrets.token_hex(24)
    originals = [_image(canary), b"OggS" + canary.encode(), canary.encode()]
    paths = [Path(media_cache.cache_image_from_bytes(originals[0], ".png")),
             Path(media_cache.cache_audio_from_bytes(originals[1])),
             Path(media_cache.cache_document_from_bytes(originals[2], "private.txt"))]
    for path, original in zip(paths, originals):
        assert path.is_relative_to(home / "artifacts" / "media")
        assert canary.encode() not in path.read_bytes()
        assert path.stat().st_mode & 0o777 == 0o600
        assert artifacts.read_bytes(path, home=home) == original
    paths[1].write_bytes(paths[0].read_bytes())
    with pytest.raises(VaultIntegrityError):
        artifacts.read_bytes(paths[1], home=home)


def test_mcp_image_and_document_recover_through_existing_consumers(home, monkeypatch):
    from tools import file_tools, mcp_tool_content

    canary = secrets.token_hex(24)
    image = _image(canary)
    block = SimpleNamespace(data=base64.b64encode(image).decode(), mimeType="image/png")
    path = mcp_tool_content._cache_mcp_image_block(block).removeprefix("MEDIA:")
    assert canary.encode() not in Path(path).read_bytes()
    part, _ = mcp_tool_content._mcp_native_image_part(path)
    native = base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
    with Image.open(BytesIO(native)) as decoded:
        assert decoded.size == (12, 8)
    document = BytesIO()
    with zipfile.ZipFile(document, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                          f'<w:body><w:p><w:r><w:t>{canary}</w:t></w:r></w:p></w:body></w:document>')
    saved = media_cache.cache_document_from_bytes(document.getvalue(), "private.docx")
    monkeypatch.setattr(file_tools, "_get_file_ops", lambda *_: pytest.fail("private media must not use remote backend"))
    # The built-in DOCX extractor is path-oriented; its copy must be proven RAM,
    # not the process's ordinary tempfile directory.
    from tools import media_artifacts
    workspace = home.parent / "synthetic-ram"
    workspace.mkdir()
    from contextlib import contextmanager

    @contextmanager
    def staged(_purpose):
        yield workspace

    monkeypatch.setattr(media_artifacts, "protected_media_workspace", staged)
    result = json.loads(file_tools.read_file_tool(saved, task_id="mcp-private"))
    assert not result.get("error") and canary in result["content"]
    assert result.get("extracted_document") is True
    assert not list(workspace.iterdir())
    assert all(canary.encode() not in p.read_bytes() for p in home.rglob("*") if p.is_file())


def test_media_limit_is_plaintext_not_ciphertext_and_other_roots_stay_bounded(home, monkeypatch):
    monkeypatch.setattr(artifacts, "_MAX_MEDIA_BYTES", 80)
    monkeypatch.setattr(artifacts, "_MAX_BYTES", 40)
    monkeypatch.setattr(media_cache, "MAX_MEDIA_BYTES", 80)
    payload = secrets.token_bytes(80)
    path = media_cache.cache_document_from_bytes(payload, "maximum.bin")
    assert len(Path(path).read_bytes()) > len(payload)
    assert artifacts.read_bytes(path) == payload
    with pytest.raises(ValueError):
        media_cache.cache_document_from_bytes(payload + b"x", "oversized.bin")
    with pytest.raises(ValueError):
        artifacts.write_bytes(home / "artifacts/tool-results/too-large.txt", payload)


def test_locked_storage_and_invalid_media_are_visible_without_raw_fallback(home):
    from tools.mcp_tool_content import _cache_mcp_image_block

    block = SimpleNamespace(type="image", data=base64.b64encode(_image("private")).decode(), mimeType="image/png")
    vault.lock_now(home)
    notice = _cache_mcp_image_block(block)
    assert "could not be retained" in notice and "MEDIA:" not in notice
    if (home / "artifacts").exists():
        assert not list((home / "artifacts").rglob("*"))
    block.data = "!not-base64!"
    assert "could not be decoded" in _cache_mcp_image_block(block)


def test_audio_consumer_stages_only_in_the_scoped_workspace_and_cleans(home, monkeypatch):
    from contextlib import contextmanager
    from tools import media_artifacts, transcription_tools

    payload = b"RIFF" + secrets.token_bytes(80)
    path = media_cache.cache_audio_from_bytes(payload, ".wav")
    workspace = home.parent / "synthetic-audio-ram"
    workspace.mkdir()

    @contextmanager
    def stage(_purpose):
        yield workspace

    monkeypatch.setattr(media_artifacts, "protected_media_workspace", stage)

    def consume(staged, _model, _source):
        assert Path(staged).is_relative_to(workspace)
        assert Path(staged).read_bytes() == payload
        assert media_artifacts.temporary_directory_options() == {"dir": str(workspace)}
        return {"success": True, "transcript": "synthetic transcript"}

    monkeypatch.setattr(transcription_tools, "_transcribe_audio_path", consume)
    assert transcription_tools.transcribe_audio(path)["success"] is True
    assert not list(workspace.iterdir())
    assert media_artifacts.temporary_directory_options() == {}
    assert artifacts.read_bytes(path) == payload


@pytest.mark.parametrize("extension", [".db", ".log", ".jsonl"])
def test_media_extension_cannot_change_repair_scan_or_offline_export_codec(home, extension):
    import tarfile
    from hermes_security.migrate import repair_clobbered_state, scan_for_plaintext
    from hermes_security.recovery_export import export_home

    payload = secrets.token_bytes(60)
    canonical = Path(media_cache.cache_document_from_bytes(payload, "resource" + extension))
    relative = Path("cache/documents") / ("doc_" + secrets.token_hex(16) + extension)
    legacy = home / relative
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(payload)
    unrelated = legacy.with_name("other-producer" + extension)
    unrelated.write_bytes(b"public fixture unrelated to MCP media")
    assert relative.as_posix() in scan_for_plaintext(home)
    repaired = repair_clobbered_state(home)
    assert repaired["unrecoverable"] == []
    assert artifacts.read_bytes(canonical) == artifacts.read_bytes(legacy) == payload
    assert scan_for_plaintext(home) == []
    assert unrelated.read_bytes() == b"public fixture unrelated to MCP media"
    output = home.parent / ("media-export" + extension + ".tar.gz")
    export_home(home, output)
    with tarfile.open(output) as archive:
        assert archive.extractfile(canonical.relative_to(home).as_posix()).read() == payload
        if relative.as_posix() in archive.getnames():
            assert archive.extractfile(relative.as_posix()).read() == payload
