"""Previously migrated TUI images keep their exact ciphertext and resume consumers."""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from hermes_security import artifacts, vault
from hermes_security.errors import VaultError
from hermes_security.migrate import repair_clobbered_state
from tools.browser_media_custody import owned_media_path, read_owned_media

PIXELS = b"\x89PNG\r\n\x1a\nsynthetic legacy TUI image payload"
PASSWORD = "synthetic legacy image credential"


@pytest.fixture
def home(tmp_path, monkeypatch):
    owner = tmp_path / "owner"
    owner.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(owner))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(owner, PASSWORD)
    vault.unlock(owner, PASSWORD)
    (owner / "images").mkdir(mode=0o700)
    yield owner
    vault.clear_vault_cache()


@pytest.mark.parametrize("name", ["upload_a1b2c3d4.png", "clip_20261103_153012_2.png",
                                 "pdf_p3_q1w2e3r4.png"])
def test_migrated_image_keeps_ciphertext_and_authenticated_consumers(home, name):
    from agent.image_routing import build_native_content_parts
    from tui_runtime import server

    relative = Path("images") / name
    target = home / relative
    # Recreate the published migration's codec, independently of the new artifact admission.
    ciphertext = vault.get_vault(home).encrypt(PIXELS, purpose="state", relpath=relative.as_posix())
    target.write_bytes(ciphertext)
    target.chmod(0o600)

    assert owned_media_path(str(target)) == target
    assert read_owned_media(target, home=home) == PIXELS
    parts, skipped = build_native_content_parts("Describe", [str(target)])
    assert skipped == []
    assert base64.b64decode(parts[1]["image_url"]["url"].split(",", 1)[1]) == PIXELS
    assert server._projected_image_bytes(target, home) == PIXELS
    assert repair_clobbered_state(home)["unrecoverable"] == []
    assert target.read_bytes() == ciphertext

    vault.lock_now(home)
    with pytest.raises(VaultError):
        read_owned_media(target, home=home)
    assert target.read_bytes() == ciphertext


def test_legacy_plaintext_requires_explicit_repair_and_wrong_paths_never_decrypt(home):
    target = home / "images" / "upload_a1b2c3d4.png"
    target.write_bytes(PIXELS)
    target.chmod(0o600)
    with pytest.raises(VaultError):
        read_owned_media(target, home=home)
    assert target.read_bytes() == PIXELS
    repaired = repair_clobbered_state(home)
    assert repaired["unrecoverable"] == []
    assert read_owned_media(target, home=home) == PIXELS
    ciphertext = target.read_bytes()
    moved = home / "images" / "upload_d4c3b2a1.png"
    moved.write_bytes(ciphertext)
    moved.chmod(0o600)
    with pytest.raises(VaultError):
        read_owned_media(moved, home=home)
    with pytest.raises(PermissionError):
        artifacts.read_bytes(home / "images" / "credentials.env", home=home)
    assert target.read_bytes() == ciphertext
