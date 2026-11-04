"""Public Ink keyboard previews authenticate images without plaintext private files.

Run this native Linux contract only in a disposable pod, as required by the TUI
guide. It uses the published bare command and real slash worker, not private RPC.
"""
from __future__ import annotations

import http.server
from io import BytesIO
import os
import shlex
import shutil
import threading

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from tests.tui_runtime.test_terminal_global_model_pty import _configure_global_home
from tests.tui_runtime.test_terminal_model_pty import (
    ROOT, ModelWire, TerminalPTY, _composer_ready, save_fixture_runtime_logs,
)

pytestmark = [pytest.mark.platforms("linux"), pytest.mark.live_system_guard_bypass]
CANARY = b"synthetic private PTY preview metadata 52e43f"


def _private_png():
    image = Image.new("RGB", (32, 16), "black")
    image.paste("white", (16, 0, 32, 16))
    metadata = PngInfo()
    metadata.add_text("synthetic-private-metadata", CANARY.decode("ascii"))
    output = BytesIO()
    image.save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


def _assert_no_plaintext(home, png):
    for path in home.rglob("*"):
        if path.is_file() and not path.is_symlink():
            data = path.read_bytes()
            assert not any(marker in data for marker in (png[:8], CANARY)), path.relative_to(home)


def test_public_ink_views_encrypted_image_once_unlocked_and_keeps_owner_usable(tmp_path, monkeypatch):
    from hermes_cli.runtime_launcher import write_cli
    from hermes_security import artifacts, vault

    node = shutil.which("node")
    if not node or not (ROOT / "ui-tui/dist/entry.js").is_file():
        if os.environ.get("HERMES_E2E_REQUIRE_TUI") == "1":
            pytest.fail("PM Node and a built Ink bundle are required for image-preview acceptance")
        pytest.skip("requires PM Node and a built Ink bundle")
    monkeypatch.setenv("HERMES_NODE", node)
    monkeypatch.delenv("HERMES_TUI_DIR", raising=False)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ModelWire)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    terminal = None
    home = password = None
    try:
        home, password = _configure_global_home(tmp_path, monkeypatch,
            f"http://127.0.0.1:{server.server_port}/v1")
        vault.unlock(home, password)
        png = _private_png()
        assert CANARY in png
        path = home / "artifacts" / "browser" / "screen with spaces.png"
        artifacts.write_bytes(path, png, home=home)
        ciphertext = path.read_bytes()
        assert not ciphertext.startswith(b"\x89PNG\r\n\x1a\n")
        commands = tmp_path / "commands"
        commands.mkdir(mode=0o700)
        write_cli(commands, ROOT)
        vault.clear_vault_cache()
        env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"],
                   NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
        for name in ("HERMES_PYTHON", "HERMES_VAULT_KEY_FD", "HERMES_SESSION_VAULT"):
            env.pop(name, None)
        terminal = TerminalPTY(env, password, tmp_path / "artifact-preview-transcript.log", argv=["hermes"])
        terminal.until(lambda _: _composer_ready(terminal), "public encrypted owner ready")
        terminal.submit("/artifacts view " + shlex.quote(str(path)) + " --cols 16 --rows 4",
                        rendered_suffix="--cols 16 --rows 4")
        terminal.until(lambda text: "Bounded grayscale preview" in text and _composer_ready(terminal),
                       "encrypted image preview rendered in Ink")
        rendered = "\n".join(terminal.screen.display())
        assert "32×16 pixels; 16×4 text cells" in rendered
        palette = set(" .:-=+*#%@")
        rows = [row.strip(" │") for row in rendered.splitlines()
                if "@" in row and set(row.strip(" │")) <= palette]
        assert len(rows) == 4 and all(1 <= len(row) <= 16 for row in rows), rendered
        assert path.read_bytes() == ciphertext
        assert not server.requests, "viewing a private image must not call the model"
        terminal.submit("after-private-preview")
        terminal.until(lambda text: "fixture-reply-1" in text and _composer_ready(terminal),
                       "same owner completed its next real model turn")
        assert len(server.requests) == 1 and server.requests[0]["model"] == "fixture-alpha"
        assert any(message["role"] == "user" and "after-private-preview" in str(message.get("content"))
                   for message in server.requests[0]["messages"])
        assert terminal.prompts == 1 and terminal.process.poll() is None
        assert CANARY.decode("ascii") not in terminal.text()
        assert path.read_bytes() == ciphertext
        terminal.submit("/quit")
        terminal.until(lambda _: terminal.process.poll() is not None, "image-preview owner exited normally")
        assert terminal.process.returncode == 0 and terminal.prompts == 1
        terminal.close()
        _assert_no_plaintext(home, png)
    finally:
        if terminal is not None:
            terminal.close()
            save_fixture_runtime_logs(home, password, tmp_path / "artifact-preview-runtime.log")
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        vault.clear_vault_cache()
