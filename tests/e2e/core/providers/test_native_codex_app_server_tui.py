"""A real local Ink TUI renders final Codex messages even without delta frames.

The native Codex bridge emits commentary and a final agentMessage. Drive the
actual PTY, local stdio runtime and AIAgent against a fake vendor executable;
assert both durable final text and visible once-only commentary/final output.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

from tests.e2e.core.providers._native_helpers import latest_session, make_home, messages
from tests.e2e.core.terminal._pty import REPO_ROOT, PtyHermes, poll
from tests.fakes.providers.codex_app_server import FakeCodex

pytestmark = [
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="real PTY + /proc session scan"),
    # Cleanup signals only this fixture's PTY session tree.
    pytest.mark.live_system_guard_bypass,
]

COMMENTARY, FINAL = "CODEX-COMMENTARY-ALPHA", "CODEX-FINAL-OMEGA"


def test_undelta_codex_messages_reach_the_attached_tui(tmp_path: Path) -> None:
    if shutil.which("node") is None or not (REPO_ROOT / "ui-tui" / "dist" / "entry.js").is_file():
        if os.environ.get("HERMES_E2E_REQUIRE_TUI") == "1":
            pytest.fail("ui-tui/dist/entry.js or node missing but HERMES_E2E_REQUIRE_TUI=1")
        pytest.skip("Ink TUI not built (cd ui-tui && npm run build) or node missing")

    fake = FakeCodex(tmp_path, [{"steps": [
        {"kind": "message", "text": COMMENTARY, "deltas": False},
        {"kind": "command", "command": "echo CANARY-1", "output": "CANARY-1-OUT\n"},
        {"kind": "message", "text": FINAL, "deltas": False},
    ]}])
    home = make_home(tmp_path, {"provider": "openai", "default": "gpt-5.5", "openai_runtime": "codex_app_server",
                                "codex_bin": str(fake.bin)}, env_file={"OPENAI_API_KEY": "sk-fake-codex-e2e"})
    tui = PtyHermes(tmp_path, ["--tui", "--yolo"], None, rows=50, cols=120)
    try:
        tui.wait_ready()
        tui.submit("run the canary USER-ONE")
        # Completion signal independent of the screen: the agent persisted the final answer.
        rows = []

        def final_persisted() -> bool:
            nonlocal rows
            try:
                rows = messages(home, latest_session(home))
            except (AssertionError, sqlite3.OperationalError):  # no db / schema not created yet
                return False
            return any(r["role"] == "assistant" and r["content"] == FINAL for r in rows)

        poll(final_persisted, timeout=90, what="the codex final answer persisted")
        tui.wait_quiet(2.0, timeout=45)
        screen = tui.dump()
        rows = [(r["role"], r["content"]) for r in rows]
        assert tui.count(FINAL) == 1, f"final answer on screen {tui.count(FINAL)}x (rows={rows}):\n{screen}"
        assert tui.count(COMMENTARY) == 1, f"commentary on screen {tui.count(COMMENTARY)}x:\n{screen}"
        assert tui.text().index(COMMENTARY) < tui.text().index(FINAL), screen
        fake.assert_wire_clean()
    finally:
        tui.close()
