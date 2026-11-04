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
    canary = secrets.token_hex(24)
    pixels, calls = _pixels(canary), []
    provider = _provider(canary, pixels, calls)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    runtime = None
    try:
        config = {"updates": {"check": False}, "security": {"protected_work_dir": ram},
                  "image_gen": {"provider": "openai", "openai": {
                      "base_url": f"http://127.0.0.1:{provider.server_port}/v1", "key_env": "PRIVATE_IMAGE_API_KEY",
                      "model": "gpt-image-2-low"}}, "display": {"pet": {"enabled": False}}}
        state_io.write_text(home / "config.yaml", json.dumps(config), purpose="config")
        state_io.write_text(home / ".env", "PRIVATE_IMAGE_API_KEY=synthetic-private-image-key\n", purpose="env")
        prepare_fixture_runtime(home, ROOT)
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
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=10)
        vault.clear_vault_cache()
