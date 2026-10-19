"""Finite public commands drain the actual deferred title writer before hard exit."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from tests.e2e.core.upgrade._helpers import kill_tree
from tests.e2e.vault_home import (
    _fixture_pm_home, connect_state, prepare_fixture_runtime, spawn_unlocked, write_encrypted_home,
)
from tests.fakes.fake_llm_provider import FakeLLMServer, Text

pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]
ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize("entry", ["chat", "z"])
def test_public_finite_command_drains_deferred_title_before_exit(tmp_path, entry, request):
    from hermes_security import vault

    home = tmp_path / "home"
    entered, release = threading.Event(), threading.Event()
    expected_title = "Deferred title persisted before exit"

    def auxiliary(_request):
        entered.set()
        assert release.wait(60), "fixture never released the held title HTTP response"
        return Text('{"title": "' + expected_title + '"}')

    with FakeLLMServer([Text("FINITE-TITLE-REPLY")], aux=auxiliary) as server:
        write_encrypted_home(home, server.base_url, extra_config=(
            "auxiliary:\n  title_generation:\n    enabled: true\n"
            "    model_upgrade_enabled: true\n    timeout: 30\n"))
        request.addfinalizer(lambda: vault.lock_now(home))
        prepare_fixture_runtime(home, ROOT)
        prompt = "Investigate reliable SQLCipher shutdown and deferred session titles"
        args = ["chat", "-q", prompt, "-Q"] if entry == "chat" else ["-z", prompt]
        with _fixture_pm_home(home):
            process = spawn_unlocked([sys.executable, "-m", "hermes_cli.main", *args],
                env={**os.environ, "HERMES_HOME": str(home)}, cwd=ROOT,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                assert entered.wait(30), "actual deferred title HTTP request never arrived"
                try:
                    process.wait(timeout=2)
                    exited_before_release = True
                except subprocess.TimeoutExpired:
                    exited_before_release = False
            finally:
                release.set()
                try:
                    stdout, stderr = process.communicate(timeout=120)
                except subprocess.TimeoutExpired:
                    kill_tree(process)
                    process.communicate(timeout=10)
                    raise
            assert not exited_before_release, (process.returncode, stdout, stderr)
            assert process.returncode == 0 and "FINITE-TITLE-REPLY" in stdout, (stdout, stderr)
            assert len(server.main_requests()) == 1 and len(server.aux_requests()) == 1
            db = connect_state(home / "state.db")
            try:
                assert [tuple(row) for row in db.execute("PRAGMA integrity_check")] == [("ok",)]
                assert [tuple(row) for row in db.execute("SELECT title FROM sessions")] == [(expected_title,)]
            finally:
                db.close()
