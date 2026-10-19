"""Public Ink naming owns encrypted history and emits real tab/window title bytes."""
from __future__ import annotations

import http.server
import json
import os
import re
import secrets
import shutil
import threading
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.e2e.vault_home import connect_state
from tests.tui_runtime.test_terminal_model_pty import (
    ROOT, ModelWire, TerminalPTY, save_fixture_runtime_logs,
)
from tests.waiting import wait_until

pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]
OSC = re.compile(rb"\x1b\]([12]);(.*?)(?:\x07|\x1b\\)", re.DOTALL)


class NamingWire(ModelWire):
    """Actual SDK HTTP, with an independently held auxiliary title response."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/show":
            self._json({})
            return
        assert self.path == "/v1/chat/completions"
        assert body["model"] in {"fixture-alpha", "fixture-title"}
        with self.server.request_lock:
            self.server.requests.append(body)
            main_count = sum(item["model"] == "fixture-alpha" for item in self.server.requests)
        if body["model"] == "fixture-title":
            title = "Automatic session title"
            if self.server.hold_title.is_set():
                self.server.title_started.set()
                if not self.server.release_title.wait(45):
                    self._json({"error": {"message": "fixture title release deadline"}}, 500)
                    return
                title = "Stale generated name"
            content = json.dumps({"title": title})
        else:
            content = f"naming-reply-{main_count}"
        self._reply(body, content)
        if body["model"] == "fixture-title" and self.server.hold_title.is_set():
            self.server.title_returned.set()

    def _reply(self, body, content):
        if not body.get("stream"):
            self._json({"id": "naming", "object": "chat.completion", "created": 1,
                "model": body["model"], "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for delta, finish in (({"role": "assistant", "content": content}, None), ({}, "stop")):
            chunk = {"id": "naming", "object": "chat.completion.chunk", "created": 1,
                "model": body["model"], "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def _osc(terminal, start=0):
    return [(int(kind), value.decode("utf-8")) for kind, value in OSC.findall(bytes(terminal.output[start:]))]


def _wait_title(terminal, title, start):
    terminal.until(lambda _text: all(any(kind == expected and title in value
        for kind, value in _osc(terminal, start)) for expected in (1, 2)), f"fresh OSC1+OSC2: {title}")


def _snapshot(home):
    with closing(connect_state(home / "state.db")) as db:
        sessions = db.execute("SELECT id, title FROM sessions WHERE source = 'cli' OR source = 'tui'").fetchall()
        assert len(sessions) == 1, [dict(row) for row in sessions]
        sid, title = sessions[0]
        rows = db.execute("SELECT role, content FROM messages WHERE session_id = ? ORDER BY id", (sid,)).fetchall()
        return sid, title, [tuple(row) for row in rows]


@dataclass
class NamingWorld:
    home: Path
    password: str
    server: object
    root: Path
    terminals: list = field(default_factory=list)
    closed: list = field(default_factory=list)

    def open(self, sid=None):
        from hermes_cli._launchers import runtime_command
        from hermes_security import vault

        vault.clear_vault_cache()
        env = dict(os.environ)
        env.pop("HERMES_PYTHON", None)
        env.pop("HERMES_VAULT_KEY_FD", None)
        argv = runtime_command(ROOT)
        if sid is not None:
            argv = [*argv, "--resume", sid]
        terminal = TerminalPTY(env, self.password, self.root / f"owner-{len(self.terminals)}.log",
            argv=argv, project=self.root / "naming-workspace")
        self.terminals.append(terminal)
        terminal.until(lambda text: "─ ready │" in text and "❯" in text, "public Ink composer")
        assert terminal.prompts == 1
        vault.unlock(self.home, self.password)
        return terminal

    def main_requests(self):
        with self.server.request_lock:
            return [request for request in self.server.requests if request["model"] == "fixture-alpha"]

    def chat(self, terminal, prompt):
        count = len(self.main_requests()) + 1
        terminal.submit(prompt)
        terminal.until(lambda text: f"naming-reply-{count}" in text
            and "─ ready │" in "\n".join(terminal.screen.display()), "completed main turn")
        return self.main_requests()[-1]

    def rename(self, terminal, command, title):
        start = len(terminal.output)
        terminal.submit(command)
        _wait_title(terminal, title, start)
        assert _snapshot(self.home)[1] == title

    def exit(self, terminal):
        terminal.submit("/quit")
        wait_until(lambda: (terminal.read(), terminal.process.poll() is not None)[1],
            timeout=45, what="graceful public terminal exit")
        assert terminal.process.returncode == 0
        assert terminal.prompts == 1
        terminal.close()
        self.closed.append(terminal)


@pytest.fixture
def naming_world(tmp_path, monkeypatch):
    from hermes_cli.config import ensure_hermes_home, save_config, save_env_value
    from hermes_security import migrate, vault
    from pm import sync_venv

    node = shutil.which("node")
    if not node or not (ROOT / "ui-tui/dist/entry.js").is_file():
        if os.environ.get("HERMES_E2E_REQUIRE_TUI") == "1":
            pytest.fail("PM Node and a built Ink bundle are required for public naming acceptance")
        pytest.skip("requires PM Node and a built Ink bundle")
    monkeypatch.setenv("HERMES_NODE", node)
    monkeypatch.delenv("HERMES_TUI_DIR", raising=False)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert home.resolve().is_relative_to(tmp_path.resolve())
    ensure_hermes_home()
    password = secrets.token_urlsafe(24)
    assert migrate.migrate_home(home, password).ok
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), NamingWire)
    server.requests = []
    server.request_lock = threading.Lock()
    server.hold_title = threading.Event()
    server.title_started = threading.Event()
    server.release_title = threading.Event()
    server.title_returned = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    world = NamingWorld(home, password, server, tmp_path)
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1"
        save_env_value("NAMING_PTY_KEY", "synthetic-naming-key")
        save_config({"onboarding": {"profile_build": "off"},
            "model": {"provider": "custom:fixture", "default": "fixture-alpha", "base_url": url,
                "context_length": 64000},
            "providers": {"fixture": {"name": "Fixture Endpoint", "base_url": url,
                "key_env": "NAMING_PTY_KEY", "api_mode": "chat_completions",
                "models": ["fixture-alpha", "fixture-title"]}},
            "auxiliary": {"title_generation": {"enabled": True, "model_upgrade_enabled": True,
                "provider": "main", "model": "fixture-title"}},
            "curator": {"enabled": False}, "updates": {"check": False},
            "display": {"tui_auto_resume_recent": False},
            "agent": {"reasoning_effort": "none"}}, merge_existing=False)
        sync_venv(extras=["all"], explicit=True, project_root=ROOT)
        vault.clear_vault_cache()
        yield world
    finally:
        server.release_title.set()
        for terminal in world.terminals:
            # close() is a scoped process-group reaper if an assertion interrupts normal /quit.
            if terminal not in world.closed:
                terminal.close()
        try:
            (tmp_path / "wire-requests.json").write_text(json.dumps(server.requests, ensure_ascii=False, indent=2))
            (tmp_path / "osc-events.json").write_text(json.dumps(
                [_osc(terminal) for terminal in world.terminals], ensure_ascii=False, indent=2))
            save_fixture_runtime_logs(home, password, tmp_path / "runtime-diagnostics.log")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            vault.clear_vault_cache()


def _assert_main_prefix(first, following):
    assert first["messages"][0]["role"] == "system"
    assert following["messages"][0] == first["messages"][0]
    assert following["messages"][:len(first["messages"])] == first["messages"]


def test_public_names_persist_across_independent_encrypted_owners(naming_world):
    world = naming_world
    first = world.open()
    _wait_title(first, "naming-workspace", 0)
    request = world.chat(first, "Remember naming-anchor-" + secrets.token_hex(8))
    _wait_title(first, "Automatic session title", 0)
    sid, _, prefix = _snapshot(world.home)
    assert any(item["model"] == "fixture-title" for item in world.server.requests)
    main_count = len(world.main_requests())
    for command, title in (("/name Explicit session name", "Explicit session name"),
            ("/rename Café 漢字", "Café 漢字"), ("/title Final persisted title", "Final persisted title")):
        world.rename(first, command, title)
        assert _snapshot(world.home) == (sid, title, prefix)
        assert len(world.main_requests()) == main_count
    world.exit(first)
    assert b"Final persisted title" not in (world.home / "state.db").read_bytes()
    second = world.open(sid)
    _wait_title(second, "Final persisted title", 0)
    assert _snapshot(world.home) == (sid, "Final persisted title", prefix)
    following = world.chat(second, "Recall the naming anchor in this resumed session.")
    _assert_main_prefix(request, following)
    assert _snapshot(world.home)[0:2] == (sid, "Final persisted title")
    assert _snapshot(world.home)[2][:len(prefix)] == prefix
    world.exit(second)


def test_manual_rename_wins_over_inflight_generated_name(naming_world):
    world = naming_world
    terminal = world.open()
    first = world.chat(terminal, "Remember rename-race-anchor-" + secrets.token_hex(8))
    _wait_title(terminal, "Automatic session title", 0)
    sid, _, prefix = _snapshot(world.home)
    world.server.hold_title.set()
    terminal.submit("/name")
    terminal.until(lambda _text: world.server.title_started.is_set(), "generated name HTTP is held")
    start = len(terminal.output)
    world.rename(terminal, "/rename Manual winner 世界", "Manual winner 世界")
    assert not world.server.title_returned.is_set()
    assert _snapshot(world.home) == (sid, "Manual winner 世界", prefix)
    assert len(world.main_requests()) == 1 and terminal.prompts == 1
    world.server.release_title.set()
    terminal.until(lambda text: world.server.title_returned.is_set()
        and "renamed while" in text, "stale generated completion rejected after manual rename")
    assert _snapshot(world.home) == (sid, "Manual winner 世界", prefix)
    assert not any("Stale generated name" in title for _, title in _osc(terminal, start))
    for kind in (1, 2):
        assert "Manual winner 世界" in [title for code, title in _osc(terminal, start) if code == kind][-1]
    assert len(world.main_requests()) == 1 and terminal.prompts == 1
    following = world.chat(terminal, "Recall the rename race anchor.")
    _assert_main_prefix(first, following)
    assert _snapshot(world.home)[0:2] == (sid, "Manual winner 世界")
    world.exit(terminal)
