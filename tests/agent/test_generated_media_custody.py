"""Only-copy generated outputs and edit references retain authenticated private bytes."""
from __future__ import annotations

import base64
import io
import secrets

import pytest
from PIL import Image, PngImagePlugin

from hermes_security import artifacts, vault
from hermes_security.errors import VaultError


@pytest.fixture
def home(tmp_path, monkeypatch):
    selected = tmp_path / "owner"
    selected.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(selected))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(selected, "synthetic generated media password")
    vault.unlock(selected, "synthetic generated media password")
    yield selected
    vault.clear_vault_cache()


def _png():
    output = io.BytesIO()
    note = PngImagePlugin.PngInfo()
    note.add_text("private", secrets.token_hex(24))
    Image.new("RGB", (3, 2), "navy").save(output, format="PNG", pnginfo=note)
    return output.getvalue()


@pytest.mark.parametrize("kind", ["image", "video"])
def test_provider_materialization_encrypts_and_preserves_the_only_copy(home, kind):
    from agent.image_gen_provider import save_b64_image
    from agent.video_gen_provider import save_bytes_video

    original = _png() if kind == "image" else b"\x00\x00\x00\x18ftypmp42" + secrets.token_bytes(64)
    path = (save_b64_image(base64.b64encode(original).decode()) if kind == "image"
            else save_bytes_video(original))
    assert path.read_bytes().startswith(b"HRMVAULT\x00")
    assert artifacts.read_bytes(path) == original
    assert all(original not in p.read_bytes() for p in home.rglob("*") if p.is_file())
    vault.lock_now(home)
    with pytest.raises(VaultError):
        artifacts.read_bytes(path)
    assert path.exists()


def test_local_edit_reads_an_owned_encrypted_image_as_original_bytes(home, monkeypatch):
    from tools.image_generation_tool import _confine_source_images
    from tools.media_cache import cache_image_from_bytes

    original = _png()
    saved = cache_image_from_bytes(original, ".png")
    monkeypatch.setattr("tools.terminal_scope.terminal_env", lambda name: "local")
    primary, references, error = _confine_source_images(saved, [saved], "private-edit")
    assert error is None
    assert primary.startswith("data:image/png;base64,")
    assert base64.b64decode(primary.partition(",")[2]) == original
    assert references == [primary]


def test_download_cannot_revive_its_original_writer_after_owner_reunlock(home, monkeypatch):
    import httpx
    from agent.image_gen_provider import save_url_image

    original = _png()
    previous = vault.get_vault(home)

    def serve(_request):
        vault.lock_now(home)
        vault.unlock(home, "synthetic generated media password")
        return httpx.Response(200, headers={"Content-Type": "image/png"}, content=original)

    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)
    monkeypatch.setattr("tools.url_safety.create_ssrf_safe_client",
                        lambda **kwargs: httpx.Client(transport=httpx.MockTransport(serve), **kwargs))
    with pytest.raises(VaultError):
        save_url_image("https://provider.example/private-result")
    with pytest.raises(VaultError):
        previous.assert_custody_live()
    assert vault.is_unlocked(home)
    assert not list((home / "artifacts").rglob("*"))
    assert all(original not in p.read_bytes() for p in home.rglob("*") if p.is_file())


def test_historical_generated_family_is_repaired_without_losing_its_only_copy(home):
    from hermes_security.migrate import repair_clobbered_state, scan_for_plaintext

    original = _png()
    path = home / "cache/generated/images" / "vendor.model_20260101_120000_deadbeef.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(original)
    neighbor = path.with_name("unrelated-public.bin")
    neighbor.write_bytes(b"public fixture")
    assert path.relative_to(home).as_posix() in scan_for_plaintext(home)
    assert repair_clobbered_state(home)["unrecoverable"] == []
    sealed = path.read_bytes()
    assert sealed.startswith(b"HRMVAULT\x00") and artifacts.read_bytes(path) == original
    assert scan_for_plaintext(home) == []
    assert repair_clobbered_state(home)["unrecoverable"] == []
    assert path.read_bytes() == sealed and neighbor.read_bytes() == b"public fixture"


def test_generated_image_rpc_and_remote_reference_keep_the_original_pixels(home, monkeypatch):
    import json
    from agent.image_gen_provider import save_b64_image
    from tools.image_generation_tool import _postprocess_image_generate_result
    from tui_runtime.methods_images import _image_to_data_url

    original = _png()
    path = save_b64_image(base64.b64encode(original).decode())
    monkeypatch.setattr("tools.image_generation_tool._active_terminal_env",
                        lambda *_: pytest.fail("encrypted artifacts must not force a remote sync"))
    result = json.loads(_postprocess_image_generate_result(json.dumps({"success": True, "image": str(path)})))
    assert result["image"] == str(path) and "agent_visible_image" not in result
    assert "owning profile" in result["artifact_access"]
    data_url = _image_to_data_url(str(path), len(original))
    assert base64.b64decode(data_url.partition(",")[2]) == original
    assert _image_to_data_url(str(path), len(original) - 1) is None


def test_generated_video_limit_preserves_its_existing_200mib_domain(home, monkeypatch):
    from agent.video_gen_provider import save_bytes_video

    monkeypatch.setattr(artifacts, "_MAX_VIDEO_BYTES", 80)
    monkeypatch.setattr(artifacts, "_MAX_MEDIA_BYTES", 40)
    original = secrets.token_bytes(80)
    path = save_bytes_video(original)
    assert len(path.read_bytes()) > 80 and artifacts.read_bytes(path) == original
    with pytest.raises(ValueError):
        save_bytes_video(original + b"x")
    with pytest.raises(ValueError):
        artifacts.write_bytes(home / "artifacts/media/documents/too-large.bin", original)


@pytest.mark.parametrize("kind", ["image", "video"])
def test_native_provider_dispatch_cannot_borrow_a_later_unlock_to_save_old_work(home, monkeypatch, kind):
    import json
    from types import SimpleNamespace
    from agent.image_gen_provider import save_b64_image
    from agent.video_gen_provider import save_bytes_video
    from tools import image_generation_tool, video_generation_tool

    original = _png()

    def save():
        return (save_b64_image(base64.b64encode(original).decode()) if kind == "image"
                else save_bytes_video(original))

    def generate(**_kwargs):
        vault.lock_now(home)
        vault.unlock(home, "synthetic generated media password")
        return {"success": True, kind: str(save())}

    provider = SimpleNamespace(name="scope-probe", generate=generate, default_model=lambda: "synthetic-model")
    if kind == "image":
        monkeypatch.setattr(image_generation_tool, "_plugin_provider_name", lambda: provider.name)
        monkeypatch.setattr(image_generation_tool, "_get_plugin_provider", lambda *_args, **_kwargs: provider)
        handler = image_generation_tool._handle_image_generate
    else:
        monkeypatch.setattr(video_generation_tool, "_read_configured_video_provider", lambda: provider.name)
        monkeypatch.setattr(video_generation_tool, "_resolve_active_provider", lambda: provider)
        handler = video_generation_tool._handle_video_generate
    result = json.loads(handler({"prompt": "synthetic private operation"}))
    assert not result["success"] and "revoked" in result["error"].lower()
    assert not list((home / "artifacts").rglob("*"))
    assert vault.is_unlocked(home)
    # Fresh work after the new human unlock remains usable.
    assert artifacts.read_bytes(save()) == original
