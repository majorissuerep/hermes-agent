"""Real private stdio/provider-wire media qualification; disposable Linux only."""
from __future__ import annotations

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import zipfile

import pytest
from PIL import Image, ImageDraw, PngImagePlugin

from hermes_security import artifacts, vault

pytestmark = pytest.mark.platforms("linux")
ROOT = Path(__file__).resolve().parents[2]


def _pixels(canary):
    image = Image.new("RGBA", (512, 128), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for offset in range(0, 512, 128):
        draw.ellipse((offset + 39, 25, offset + 91, 104), fill=(38, 83, 151, 255))
        draw.ellipse((offset + 49, 39, offset + 56, 47), fill="white")
        draw.ellipse((offset + 73, 39, offset + 80, 47), fill="white")
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private", canary)
    output = io.BytesIO()
    image.save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


def _provider(canary, pixels, calls):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or "0")
            if not 0 < length < 4 * 1024 * 1024:
                self.send_error(413)
                return
            body = self.rfile.read(length)
            calls.append({"path": self.path, "private_prompt_received": canary.encode() in body,
                          "authorized": self.headers.get("Authorization") == "Bearer synthetic-private-image-key",
                          "reference_received": "multipart/form-data" in str(self.headers.get("Content-Type"))})
            response = json.dumps({"created": 1, "data": [{"b64_json": base64.b64encode(pixels).decode()}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_args):
            return

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def test_native_generated_media_and_pet_roundtrip_over_private_stdio(tmp_path, monkeypatch):
    from hermes_security import io as state_io
    from hermes_security.protected_workspace import protected_workspace_status
    from tests.e2e.core.security._stdio import NativeRuntime
    from tests.e2e.vault_home import fixture_password, prepare_fixture_runtime, seal_fixture_home
    from hermes_security.session_authority import start_session_authority

    ram = os.environ.get("HERMES_TEST_PRIVATE_MEDIA_RAM_BASE")
    if not ram:
        pytest.skip("native media qualification requires the driver's actual protected RAM directory")
    assert protected_workspace_status(ram).proven
    user_home = tmp_path / "synthetic-home"
    home = user_home / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    home.mkdir(parents=True)
    seal_fixture_home(home)
    canary = secrets.token_hex(24)
    pixels, calls = _pixels(canary), []
    provider = _provider(canary, pixels, calls)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    runtime = None
    owner = None
    try:
        config = {"updates": {"check": False}, "security": {"protected_work_dir": ram},
                  "image_gen": {"provider": "openai", "openai": {
                      "base_url": f"http://127.0.0.1:{provider.server_port}/v1", "key_env": "PRIVATE_IMAGE_API_KEY",
                      "model": "gpt-image-2-low"}}, "display": {"pet": {"enabled": False}}}
        state_io.write_text(home / "config.yaml", json.dumps(config), purpose="config")
        state_io.write_text(home / ".env", "PRIVATE_IMAGE_API_KEY=synthetic-private-image-key\n", purpose="env")
        prepare_fixture_runtime(home, ROOT)
        owner = start_session_authority(home, "native-pet-owner", pipe=True)
        assert owner is not None
        # Console stderr may legitimately contain the synthetic private prompt;
        # the harness retains it only on the independently proven RAM mount.
        with tempfile.TemporaryDirectory(prefix="pet-native-stderr-", dir=ram) as logging:
            runtime = NativeRuntime(user_home, Path(logging) / "stderr.log")
            generated = runtime.ok("image.generate", {"prompt": canary, "aspect_ratio": "square"}, timeout=90)
            assert generated.get("success") and generated.get("image_data")
            path = Path(generated["image"])
            assert path.read_bytes().startswith(b"HRMVAULT\x00")
            decoded = base64.b64decode(generated["image_data"].partition(",")[2])
            assert hashlib.sha256(decoded).digest() == hashlib.sha256(pixels).digest()
            draft = runtime.ok("pet.generate", {"prompt": canary, "count": 1, "provider": "openai"}, timeout=120)
            assert draft.get("ok") and len(draft.get("drafts", [])) == 1
            staged = home / "artifacts/generated/pet-drafts" / draft["token"] / "draft-0.png"
            assert staged.read_bytes().startswith(b"HRMVAULT\x00")
            hatched = runtime.ok("pet.hatch", {"token": draft["token"], "index": 0, "name": "Native animal",
                                               "description": canary, "prompt": canary, "provider": "openai"}, timeout=180)
            assert hatched.get("ok") and hatched.get("pet", {}).get("spritesheetBase64")
            old_slug = hatched["slug"]
            assert runtime.ok("pet.select", {"slug": old_slug}).get("ok")
            before = runtime.ok("pet.info", {})
            assert before.get("enabled") and before.get("spritesheetBase64")
            renamed = runtime.ok("pet.rename", {"slug": old_slug, "name": "Private label " + canary})
            assert renamed.get("ok") and renamed["slug"] != old_slug
            after = runtime.ok("pet.info", {})
            assert after.get("enabled") and after.get("slug") == renamed["slug"]
            assert hashlib.sha256(after["spritesheetBase64"].encode()).digest() == hashlib.sha256(before["spritesheetBase64"].encode()).digest()
            thumb = runtime.ok("pet.thumb", {"slug": renamed["slug"]})
            assert thumb.get("ok") and thumb.get("dataUri", "").startswith("data:image/png;base64,")
            exported = runtime.ok("pet.export", {"slug": renamed["slug"]})
            assert exported.get("ok")
            with zipfile.ZipFile(io.BytesIO(base64.b64decode(exported["zipBase64"]))) as archive:
                metadata = json.loads(archive.read(renamed["slug"] + "/pet.json"))
                assert metadata["description"] == canary
            foreign = runtime.call("pet.info", {"profile": "foreign"})
            assert not (foreign.get("result") or {}).get("spritesheetBase64")
            vault.lock_now(home)
            vault.unlock(home, fixture_password(home))
            revoked = runtime.call("pet.info", {}, timeout=15)
            assert "error" in revoked and not (revoked.get("result") or {}).get("spritesheetBase64")
            runtime.close()
            assert runtime.proc.poll() is not None
            runtime = NativeRuntime(user_home, Path(logging) / "reopened.log")
            reopened = runtime.ok("pet.info", {})
            assert reopened.get("slug") == renamed["slug"]
            assert reopened["spritesheetBase64"] == after["spritesheetBase64"]
            runtime.close()
            assert runtime.proc.poll() is not None
            runtime = None
        assert calls and all(call["authorized"] for call in calls)
        assert sum(call["private_prompt_received"] for call in calls) >= 2
        assert all(call["path"] in {"/v1/images/generations", "/v1/images/edits"} for call in calls)
        assert any(call["reference_received"] for call in calls)
        assert all(canary.encode() not in p.read_bytes() and canary not in str(p)
                   for p in home.rglob("*") if p.is_file() and not p.is_symlink())
        assert vault.is_unlocked(home)
        assert artifacts.read_bytes(path, home=home) == pixels
    finally:
        if runtime is not None:
            runtime.close()
        if owner is not None:
            owner.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=10)
        vault.clear_vault_cache()


def test_public_repair_cli_converts_legacy_pet_then_a_cold_stdio_reader_recovers_it(tmp_path, monkeypatch):
    from hermes_security import io as state_io
    from hermes_security.protected_workspace import protected_workspace_status
    from tests.e2e.core.security._helpers import run_hermes
    from tests.e2e.core.security._stdio import NativeRuntime
    from tests.e2e.vault_home import prepare_fixture_runtime, seal_fixture_home

    ram = os.environ.get("HERMES_TEST_PRIVATE_MEDIA_RAM_BASE")
    if not ram:
        pytest.skip("native media qualification requires the driver's actual protected RAM directory")
    assert protected_workspace_status(ram).proven
    user_home = tmp_path / "synthetic-home"
    home = user_home / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    home.mkdir(parents=True)
    seal_fixture_home(home)
    state_io.write_text(home / "config.yaml", json.dumps({"updates": {"check": False},
                        "security": {"protected_work_dir": ram},
                        "display": {"pet": {"enabled": True, "slug": "Legacy.ID"}}}), purpose="config")
    prepare_fixture_runtime(home, ROOT)
    canary = secrets.token_hex(24)
    original = _pixels(canary)
    legacy = home / "pets/Legacy.ID"
    legacy.mkdir(parents=True)
    (legacy / "pet.json").write_text(json.dumps({"id": "Legacy.ID", "displayName": canary,
            "spritesheetPath": "spritesheet.png", "unknownPrivateField": {"note": canary}}))
    (legacy / "spritesheet.png").write_bytes(original)
    repaired = run_hermes(["secure-vault", "repair"], user_home, timeout=90)
    assert repaired.returncode == 0, (repaired.returncode, "repair did not complete")
    assert not legacy.exists()
    before = {p: p.read_bytes() for p in (home / "artifacts/generated/pet-store").rglob("*") if p.is_file()}
    repeated = run_hermes(["secure-vault", "repair"], user_home, timeout=90)
    assert repeated.returncode == 0 and all(p.read_bytes() == raw for p, raw in before.items())
    with tempfile.TemporaryDirectory(prefix="pet-repair-native-", dir=ram) as logging:
        runtime = NativeRuntime(user_home, Path(logging) / "stderr.log")
        try:
            pet = runtime.ok("pet.info", {})
            assert pet.get("slug") == "Legacy.ID" and pet.get("displayName") == canary
            assert base64.b64decode(pet["spritesheetBase64"]) == original
            exported = runtime.ok("pet.export", {"slug": "Legacy.ID"})
            with zipfile.ZipFile(io.BytesIO(base64.b64decode(exported["zipBase64"]))) as archive:
                metadata = json.loads(archive.read("Legacy.ID/pet.json"))
                assert metadata["unknownPrivateField"] == {"note": canary}
                assert archive.read("Legacy.ID/spritesheet.png") == original
        finally:
            runtime.close()
    assert all(canary.encode() not in p.read_bytes() and canary not in str(p)
               for p in home.rglob("*") if p.is_file() and not p.is_symlink())
    assert vault.is_unlocked(home)
    vault.clear_vault_cache()


def test_native_catalog_install_private_rename_reinstall_and_export(tmp_path, monkeypatch):
    """Unauthenticated public petdex service evidence, separate from loopback provider fidelity."""
    from hermes_security import io as state_io
    from tests.e2e.core.security._stdio import NativeRuntime
    from tests.e2e.vault_home import prepare_fixture_runtime, seal_fixture_home

    ram = os.environ.get("HERMES_TEST_PRIVATE_MEDIA_RAM_BASE")
    if not ram:
        pytest.skip("native media qualification requires the driver's actual protected RAM directory")
    user_home = tmp_path / "synthetic-home"
    home = user_home / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    home.mkdir(parents=True)
    seal_fixture_home(home)
    state_io.write_text(home / "config.yaml", json.dumps({"updates": {"check": False},
                        "security": {"protected_work_dir": ram}}), purpose="config")
    prepare_fixture_runtime(home, ROOT)
    with tempfile.TemporaryDirectory(prefix="pet-catalog-native-", dir=ram) as logging:
        runtime = NativeRuntime(user_home, Path(logging) / "stderr.log")
        try:
            gallery = runtime.ok("pet.gallery", {}, timeout=60)
            assert gallery.get("pets"), "Public catalog service unavailable; no catalog qualification"
            slug = gallery["pets"][0]["slug"]
            assert runtime.ok("pet.select", {"slug": slug}, timeout=90).get("ok")
            first = runtime.ok("pet.info", {})
            canary = secrets.token_hex(24)
            renamed = runtime.ok("pet.rename", {"slug": slug, "name": "Private catalog label " + canary})
            assert renamed.get("ok") and renamed["slug"] != slug
            selected = runtime.ok("pet.info", {})
            assert selected.get("slug") == renamed["slug"] and selected["spritesheetBase64"] == first["spritesheetBase64"]
            assert runtime.ok("pet.select", {"slug": slug}, timeout=90).get("ok")
            installed = runtime.ok("pet.gallery", {"localOnly": True})
            assert {slug, renamed["slug"]} <= {p["slug"] for p in installed["pets"]}
            exported = runtime.ok("pet.export", {"slug": renamed["slug"]})
            with zipfile.ZipFile(io.BytesIO(base64.b64decode(exported["zipBase64"]))) as archive:
                metadata = json.loads(archive.read(renamed["slug"] + "/pet.json"))
                assert metadata["displayName"] == "Private catalog label " + canary
                assert archive.read(renamed["slug"] + "/" + metadata["spritesheetPath"]) == base64.b64decode(first["spritesheetBase64"])
            assert all(canary.encode() not in p.read_bytes() and canary not in str(p)
                       for p in home.rglob("*") if p.is_file() and not p.is_symlink())
        finally:
            runtime.close()
    assert vault.is_unlocked(home)
    vault.clear_vault_cache()
