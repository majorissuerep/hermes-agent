"""Public Ink keyboard flow preserves automatic verification and new-session toggles."""

import os

import pytest

from hermes_platform.resolver import locate_command

from tests.e2e.core.formal_logic.test_native_checkpoints import _answer, _checkpoint
from tests.fakes.fake_llm_provider import FakeLLMServer, Text
from tests.tui_runtime.test_terminal_global_model_pty import _configure_global_home
from tests.tui_runtime.test_terminal_model_pty import ROOT, TerminalPTY, _composer_ready

pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]


def test_public_terminal_verifies_repairs_and_disables_mode_for_next_owner(tmp_path, monkeypatch):
    import json
    from hermes_cli._launchers import runtime_command
    from hermes_cli.config import load_config_readonly, save_config
    from hermes_security import vault

    node, lean = locate_command("node").command, locate_command("lean").command
    assert node and lean and (ROOT / "ui-tui/dist/entry.js").is_file(), "Build Ink and install pinned Lean first"
    monkeypatch.setenv("HERMES_NODE", node[0])
    monkeypatch.delenv("HERMES_TUI_DIR", raising=False)
    seen = []
    def main(record):
        seen.append(record["body"])
        return Text(_answer(_checkpoint(valid=len(seen) > 1))) if len(seen) <= 2 else Text("PLAIN_MODE_CONFIRMED")
    def auxiliary(record):
        return Text(json.dumps({"alignment": "faithful", "reason": "Explicit task givens",
            "assumptions": [{"id": name, "verdict": "supported", "citations": [], "reason": "Explicit stipulation"}
                            for name in ("a", "b")]}))

    terminals = []
    with FakeLLMServer(main, aux=auxiliary) as server:
        home, password = _configure_global_home(tmp_path, monkeypatch, server.base_url)
        try:
            vault.unlock(home, password)
            config = load_config_readonly()
            config["agent"]["formal_logic"] = {"enabled": True, "lean_path": lean[0], "max_repairs": 3}
            save_config(config, merge_existing=False)
            vault.clear_vault_cache()
            environment = dict(os.environ)
            environment.pop("HERMES_PYTHON", None)
            environment.pop("HERMES_VAULT_KEY_FD", None)
            first = TerminalPTY(environment, password, tmp_path / "logic-on.log", argv=runtime_command(ROOT))
            terminals.append(first)
            first.until(lambda text: "─ ready │" in text and "❯" in text, "formal owner composer")
            first.submit("Assume P; P implies Q. Show Q.")
            first.until(lambda text: "Formal check: Lean 4.35.0" in text and _composer_ready(first),
                        "verified formal answer settled")
            assert len(seen) == 2 and first.prompts == 1
            assert any("invalid_deduction" in str(m.get("content")) for m in seen[1]["messages"])
            first.submit("/quit")
            first.until(lambda text: first.process.poll() is not None, "formal owner exited")
            assert first.process.returncode == 0
            first.close()
            terminals.remove(first)
            vault.unlock(home, password)
            config = load_config_readonly()
            config["agent"]["formal_logic"]["enabled"] = False
            save_config(config, merge_existing=False)
            assert b"formal_logic" not in (home / "config.yaml").read_bytes()
            vault.clear_vault_cache()
            second = TerminalPTY(environment, password, tmp_path / "logic-off.log", argv=runtime_command(ROOT))
            terminals.append(second)
            second.until(lambda text: "─ ready │" in text and "❯" in text, "plain owner composer")
            second.submit("Reply with the plain mode marker.")
            second.until(lambda text: "PLAIN_MODE_CONFIRMED" in text and _composer_ready(second), "plain reply settled")
            assert len(seen) == 3 and second.prompts == 1
            assert not any("Experimental formal logic mode is active" in str(m.get("content")) for m in seen[2]["messages"])
            second.submit("/quit")
            second.until(lambda text: second.process.poll() is not None, "plain owner exited")
            assert second.process.returncode == 0
        finally:
            for terminal in terminals:
                terminal.transcript.write_text(terminal.text())
                terminal.close()
            vault.clear_vault_cache()
