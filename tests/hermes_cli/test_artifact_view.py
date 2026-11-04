"""Private image previews use public CLI/slash transport without plaintext files."""
from __future__ import annotations

from io import BytesIO
import shlex
import subprocess

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from hermes_cli.artifacts import preview, validate_native_arguments
from hermes_security import artifacts, vault
from hermes_security.errors import VaultLockedError
from tests.hermes_cli.test_management_public_cli import ROOT

pytest_plugins = ["tests.hermes_cli.test_management_public_cli"]
pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]


def _image():
    image = Image.new("RGB", (32, 16), "white")
    for x in range(16):
        for y in range(16):
            image.putpixel((x, y), (0, 0, 0))
    metadata = PngInfo()
    metadata.add_text("synthetic", "private screenshot preview canary 5814b")
    buffer = BytesIO()
    image.save(buffer, format="PNG", pnginfo=metadata)
    return buffer.getvalue()


@pytest.fixture
def image_home(tmp_path, monkeypatch):
    home = tmp_path / "owner"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(home, "synthetic artifact view password")
    vault.unlock(home, "synthetic artifact view password")
    path = home / "artifacts" / "browser" / "screen with spaces.png"
    artifacts.write_bytes(path, _image(), home=home)
    yield home, path
    vault.clear_vault_cache()


def test_in_memory_preview_is_bounded_and_preserves_ciphertext(image_home):
    home, path = image_home
    before = path.read_bytes()
    result = preview(str(path), cols=16, rows=4)
    assert "Bounded grayscale preview" in result
    lines = result.split("```text\n")[1].split("\n```")[0].splitlines()
    assert len(lines) <= 4 and all(len(line) <= 16 for line in lines)
    assert any("@" in line and " " in line for line in lines)
    assert path.read_bytes() == before
    assert all(b"private screenshot preview canary" not in p.read_bytes() for p in home.rglob("*") if p.is_file())
    vault.lock_now(home)
    with pytest.raises(VaultLockedError):
        preview(str(path))


def test_foreign_image_and_decode_bounds_refuse_without_mutation(image_home, tmp_path, monkeypatch):
    from hermes_cli import artifacts as renderer

    _home, path = image_home
    other = tmp_path / "other-owner"
    vault.init_vault(other, "synthetic other owner")
    vault.unlock(other, "synthetic other owner")
    foreign = other / "artifacts" / "browser" / "foreign.png"
    artifacts.write_bytes(foreign, _image(), home=other)
    with pytest.raises(PermissionError, match="different profile"):
        preview(str(foreign))
    original = path.read_bytes()
    with monkeypatch.context() as scoped:
        scoped.setattr(renderer, "_MAX_PIXELS", 1)
        with pytest.raises(ValueError, match="pixel"):
            preview(str(path))
    with monkeypatch.context() as scoped:
        scoped.setattr(renderer, "_MAX_INPUT_BYTES", 1)
        with pytest.raises(ValueError, match="input"):
            preview(str(path))
    assert path.read_bytes() == original


@pytest.mark.parametrize("relative", ["cache/screenshots/legacy.png", "browser_screenshots/legacy.png"])
def test_legacy_image_reference_requires_authenticated_state(image_home, relative):
    from hermes_security import io
    from hermes_security.errors import VaultError

    home, _ = image_home
    path = home / relative
    for parent in reversed(path.parents):
        if parent.is_relative_to(home):
            parent.mkdir(mode=0o700, exist_ok=True)
    io.write_bytes(path, _image(), purpose="state")
    assert "Bounded grayscale preview" in preview(str(path))
    path.write_bytes(_image())
    with pytest.raises(VaultError):
        preview(str(path))


@pytest.mark.parametrize("arguments", [
    ["export", "anywhere"], ["view", "/tmp/x", "--output", "/tmp/y"],
    ["view", "/tmp/x", "--cols", "121"], ["view", "/tmp/x", "--rows", "61"],
    ["view", "/tmp/x", "--col", "40"],
])
def test_borrowed_view_grammar_does_not_expand_execution_or_export(arguments):
    with pytest.raises(ValueError):
        validate_native_arguments(arguments)


@pytest.mark.parametrize("family", ["artifacts/browser", "artifacts/media/images"])
def test_public_v2_cli_and_real_slash_worker_preview_encrypted_image(world, family):
    from hermes_cli._launchers import runtime_command
    from tui_runtime import server

    path = world.home / family / "screen with spaces.png"
    artifacts.write_bytes(path, _image(), home=world.home)
    before = path.read_bytes()
    assert "HERMES_VAULT_KEY_FD" not in world.env
    result = subprocess.run(runtime_command(ROOT, ["artifacts", "view", str(path), "--cols", "16", "--rows", "4"]),
                            env=world.env, cwd=ROOT, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, world.diagnostic((result.stdout, result.stderr))
    assert "Bounded grayscale preview" in result.stdout and "```text" in result.stdout
    foreign = subprocess.run(runtime_command(ROOT, ["artifacts", "view", str(world.home.parent / "other" / path.name)]),
                             env=world.env, cwd=ROOT, capture_output=True, text=True, timeout=60, check=False)
    assert foreign.returncode != 0 and "Bounded grayscale preview" not in foreign.stdout
    worker = server._SlashWorker(world.authority.session_id, "", profile_home=str(world.home))
    try:
        rendered = worker.run("/artifacts view " + shlex.quote(str(path)) + " --cols 16 --rows 4")
        assert "Bounded grayscale preview" in rendered and "```text" in rendered
        assert "@" in rendered and "\x1b" not in rendered
    finally:
        worker.close()
    assert path.read_bytes() == before
    assert all(b"private screenshot preview canary" not in p.read_bytes()
               for p in world.home.rglob("*") if p.is_file() and not p.is_symlink())
    assert world.authority.is_live() and vault.is_unlocked(world.home)
