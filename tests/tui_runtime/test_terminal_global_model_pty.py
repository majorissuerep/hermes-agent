"""A public global model choice survives owner exit without transferring unlock authority."""
from __future__ import annotations

import http.server
import os
import secrets
import shutil
import threading

import pytest

from tests.tui_runtime.test_terminal_model_pty import ModelWire, ROOT, TerminalPTY

pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]


def _configure_global_home(tmp_path, monkeypatch, url):
    from hermes_cli.config import ensure_hermes_home, save_config, save_env_value
    from hermes_security import migrate, vault
    from pm import sync_venv

    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    ensure_hermes_home()
    password = secrets.token_urlsafe(24)
    assert migrate.migrate_home(home, password).ok
    save_env_value("MODEL_GLOBAL_PTY_KEY", "synthetic-global-key")
    save_config({"onboarding": {"profile_build": "off"},
        "model": {"provider": "custom:fixture", "default": "fixture-alpha", "base_url": url,
                  "context_length": 64000},
        "providers": {"fixture": {"name": "Fixture Endpoint", "base_url": url,
            "key_env": "MODEL_GLOBAL_PTY_KEY", "api_mode": "chat_completions",
            "models": ["fixture-alpha", "fixture-beta", "fixture-gamma"]}},
        "curator": {"enabled": False}, "updates": {"check": False},
        "auxiliary": {"title_generation": {"enabled": False}},
        "display": {"tui_auto_resume_recent": False},
        "agent": {"reasoning_effort": "none"}}, merge_existing=False)
    sync_venv(extras=["all"], explicit=True, project_root=ROOT)
    vault.clear_vault_cache()
    return home, password


def test_global_model_survives_independent_public_terminal_owner(tmp_path, monkeypatch):
    node = shutil.which("node")
    if not node or not (ROOT / "ui-tui/dist/entry.js").is_file():
        if os.environ.get("HERMES_E2E_REQUIRE_TUI") == "1":
            pytest.fail("PM Node and a built Ink bundle are required for this acceptance contract")
        pytest.skip("requires PM Node and a built Ink bundle")
    monkeypatch.setenv("HERMES_NODE", node)
    monkeypatch.delenv("HERMES_TUI_DIR", raising=False)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ModelWire)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    terminals = []
    from hermes_security import vault

    try:
        home, password = _configure_global_home(tmp_path, monkeypatch,
            f"http://127.0.0.1:{server.server_port}/v1")
        from hermes_cli._launchers import runtime_command
        from hermes_cli.config import load_config_readonly

        env = dict(os.environ)
        env.pop("HERMES_PYTHON", None)
        env.pop("HERMES_VAULT_KEY_FD", None)
        anchor = "old-owner-" + secrets.token_hex(12)
        before = (home / "config.yaml").read_bytes()
        first = TerminalPTY(env, password, tmp_path / "global-first.log", argv=runtime_command(ROOT))
        terminals.append(first)
        first.until(lambda text: "─ ready │" in text and "❯" in text, "first public composer")
        first.submit(anchor)
        first.until(lambda text: "fixture-reply-1" in text, "initial alpha request")
        assert server.requests[-1]["model"] == "fixture-alpha"
        first.submit("/model fixture-beta --global --reasoning high")
        first.until(lambda text: "model → fixture-beta" in text, "global switch acknowledged")
        first.close()
        terminals.remove(first)
        assert first.prompts == 1
        ciphertext = (home / "config.yaml").read_bytes()
        assert ciphertext != before and b"fixture-beta" not in ciphertext
        # An explicit synthetic unlock reads the real encrypted profile; no session grant is reused.
        vault.unlock(home, password)
        config = load_config_readonly()
        assert config["model"]["default"] == "fixture-beta"
        assert config["agent"]["reasoning_effort"] == "high"
        vault.clear_vault_cache()
        second = TerminalPTY(env, password, tmp_path / "global-independent.log", argv=runtime_command(ROOT))
        terminals.append(second)
        second.until(lambda text: "─ ready │" in text and "❯" in text, "independent public composer")
        assert second.process.pid != first.process.pid and second.prompts == 1
        second.submit("new-owner-" + secrets.token_hex(12))
        second.until(lambda text: "fixture-reply-2" in text, "persisted global route request")
        assert len(server.requests) == 2
        assert server.requests[-1]["model"] == "fixture-beta"
        assert server.requests[-1].get("reasoning_effort") == "high"
        assert anchor not in str(server.requests[-1]["messages"])
        assert second.prompts == 1
    finally:
        for terminal in terminals:
            terminal.transcript.write_text(terminal.text())
            terminal.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        vault.clear_vault_cache()
