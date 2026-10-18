"""Real Ink keyboard model controls must change the local agent route and retain history."""
from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import secrets
import select
import signal
import shutil
import struct
import subprocess
import sys
import threading
import time

import pytest

from tests.waiting import wait_until
from tests.e2e.core.terminal._vt import Screen

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.platforms("linux", "macos")


class ModelWire(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self._json({"object": "list", "data": [
            {"id": model, "object": "model", "owned_by": "fixture"}
            for model in ("fixture-alpha", "fixture-beta", "fixture-gamma")]})

    def _json(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/show":
            self._json({})
            return
        assert self.path == "/v1/chat/completions"
        assert body["model"] in ("fixture-alpha", "fixture-beta", "fixture-gamma")
        self.server.requests.append(body)
        text = "fixture-reply-" + str(len(self.server.requests))
        if not body.get("stream"):
            self._json({"id": "fixture", "object": "chat.completion", "created": 1,
                "model": body["model"], "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": text}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for delta, finish in (({"role": "assistant", "content": text}, None), ({}, "stop")):
            chunk = {"id": "fixture", "object": "chat.completion.chunk", "created": 1,
                "model": body["model"], "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class TerminalPTY:
    def __init__(self, env, password, transcript, *, args=()):
        import fcntl
        import pty
        import termios

        self.password = password
        self.transcript = transcript
        self.prompts = 0
        self.output = bytearray()
        self.screen = Screen(42, 140)
        project = Path(env["HOME"]) / "foreign-project"
        project.mkdir(parents=True, exist_ok=True)
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 42, 140, 0, 0))
        self.process = subprocess.Popen([sys.executable, "-m", "hermes_cli.main", *args],
            stdin=slave, stdout=slave, stderr=slave, cwd=project,
            env={**env, "TERM": "xterm-256color"}, start_new_session=True,
            preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))  # noqa: PLW1509 -- child requires its controlling PTY
        os.close(slave)

    def read(self):
        if select.select([self.master], [], [], 0.1)[0]:
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                chunk = b""
            self.output.extend(chunk)
            self.screen.feed(chunk)
            if b"\x1b[6n" in chunk:
                os.write(self.master, b"\x1b[1;1R")
            if self.prompts == 0 and b"Unlock master password:" in self.output:
                self.prompts += 1
                os.write(self.master, self.password.encode() + b"\n")
            self.transcript.write_text(self.text().replace(self.password, "[REDACTED]"))
        return self.text()

    def text(self):
        return "\n".join(self.screen.history + self.screen.display())

    def until(self, predicate, description):
        print("PTY waiting:", description, flush=True)
        def ready():
            text = self.read()
            if "Hermes could not start the assistant" in text:
                raise AssertionError(f"native agent initialization failed: {text[-6000:]}")
            if "the command helper crashed" in text:
                raise AssertionError(f"native slash child failed: {text[-6000:]}")
            if predicate(text):
                return True
            if self.process.poll() is not None:
                raise AssertionError(f"terminal exited {self.process.returncode}: {description}\n{text[-6000:]}")
            return False
        try:
            return wait_until(ready, timeout=45, what=description)
        except AssertionError as error:
            raise AssertionError(str(error) + "\n" + self.text()[-6000:]) from error

    def submit(self, text):
        os.write(self.master, text.encode())
        # Model actual separate keypresses: <50ms text+Enter is intentionally treated as paste.
        self.until(lambda output: " ".join(text.split()) in " ".join(output.split()), "typed text rendered")
        rendered = time.monotonic()
        self.until(lambda _output: time.monotonic() - rendered >= 0.15,
                   "rendered text clear of the rapid-input paste window")
        os.write(self.master, b"\r")

    def key(self, key):
        os.write(self.master, key)

    def filter(self, query):
        for index, char in enumerate(query, 1):
            self.key(char.encode())
            self.until(lambda text: f"filter: {query[:index]}" in text, "filter keystroke rendered")

    def close(self):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)
        self.process.wait(timeout=10)
        os.close(self.master)


@pytest.mark.parametrize("lazy", [False, True], ids=["active-history", "before-first-chat"])
def test_public_ink_model_picker_and_scoped_switches(tmp_path, monkeypatch, lazy):
    node = shutil.which("node")
    if not node or not (ROOT / "ui-tui/dist/entry.js").is_file():
        if os.environ.get("HERMES_E2E_REQUIRE_TUI") == "1":
            pytest.fail("PM Node and a built Ink bundle are required for this acceptance contract")
        pytest.skip("requires PM Node and a built Ink bundle")
    monkeypatch.setenv("HERMES_NODE", node)
    monkeypatch.delenv("HERMES_TUI_DIR", raising=False)
    from hermes_cli.config import ensure_hermes_home, save_config, save_env_value
    from hermes_security import migrate, vault

    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    ensure_hermes_home()
    password = secrets.token_urlsafe(24)
    assert migrate.migrate_home(home, password).ok
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ModelWire)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/v1"
    save_env_value("MODEL_PTY_KEY", "synthetic-fixture-key")
    save_config({"onboarding": {"profile_build": "off"},
        "model": {"provider": "custom:fixture", "default": "fixture-alpha",
            "base_url": url, "context_length": 64000},
        "providers": {"fixture": {"name": "Fixture Endpoint", "base_url": url,
            "key_env": "MODEL_PTY_KEY", "api_mode": "chat_completions",
            "models": ["fixture-alpha", "fixture-beta", "fixture-gamma"]}},
        "curator": {"enabled": False}, "updates": {"check": False},
        "auxiliary": {"title_generation": {"enabled": False}},
        "display": {"details_mode": "expanded"},
        "agent": {"reasoning_effort": "none"}}, merge_existing=False)
    vault.clear_vault_cache()
    terminal = TerminalPTY(dict(os.environ), password, tmp_path / "terminal-model-transcript.log")
    anchor = "history-" + secrets.token_hex(10)
    try:
        terminal.until(lambda text: "─ ready │" in text and "❯" in text, "Ink composer ready")
        before = (home / "config.yaml").read_bytes()
        terminal.submit("/config")
        terminal.until(lambda text: "(^_^) Configuration" in text, "real slash child config output")
        terminal.key(b"q")
        terminal.until(lambda text: "Config File:" not in "\n".join(terminal.screen.display())
                       and "─ ready │" in text, "config pager closed")
        if not lazy:
            terminal.submit(anchor)
            terminal.until(lambda text: "fixture-reply-1" in text, "first local agent reply")
        terminal.submit("/model")
        terminal.until(lambda text: "Select provider (step 1/3)" in text, "provider picker rendered")
        terminal.key(b"\x1b[200~Fixture\x1b[201~")
        terminal.until(lambda text: "filter: Fixture" in text, "provider filter rendered")
        terminal.key(b"\r")
        terminal.until(lambda text: "Select model (step 2/3)" in text, "model picker rendered")
        terminal.filter("f")
        terminal.key(b"\x1b[200~ixture-beta\x1b[201~")
        terminal.until(lambda text: "filter: fixture-beta" in text, "model filter rendered")
        terminal.key(b"\r")
        terminal.until(lambda text: "Reasoning effort (step 3/3)" in text, "reasoning picker rendered")
        terminal.key(b"\r")
        terminal.until(lambda text: "model → fixture-beta" in text, "picker switch settlement")
        terminal.submit(anchor if lazy else "after-picker")
        count = 1 if lazy else 2
        terminal.until(lambda text: f"fixture-reply-{count}" in text, "post-picker reply")
        terminal.submit("/model fixture-gamma --session --reasoning none")
        terminal.until(lambda text: "model → fixture-gamma" in text, "typed switch")
        terminal.submit("after-direct")
        terminal.until(lambda text: f"fixture-reply-{count + 1}" in text, "post-direct reply")
        assert [request["model"] for request in server.requests][-2:] == ["fixture-beta", "fixture-gamma"]
        history = server.requests[-1]["messages"]
        wire = [{"model": request["model"], "reasoning_effort": request.get("reasoning_effort"),
                 "messages": [row for row in request["messages"] if row["role"] in {"user", "assistant"}]}
                for request in server.requests]
        assert any(row["role"] == "user" and anchor in str(row.get("content")) for row in history), wire
        assert any(row["role"] == "assistant" and "fixture-reply-1" in str(row.get("content")) for row in history)
        terminal.submit("/model fixture-beta --once --reasoning high")
        terminal.until(lambda text: "model → fixture-beta" in text, "one-turn switch settlement")
        for offset in (2, 3):
            terminal.submit(f"once-check-{offset}")
            terminal.until(lambda text: f"fixture-reply-{count + offset}" in text, "one-turn route reply")
        assert [request["model"] for request in server.requests][-2:] == ["fixture-beta", "fixture-gamma"]
        assert server.requests[-2].get("reasoning_effort") == "high"
        assert server.requests[-1].get("reasoning_effort") in (None, "none")
        assert (home / "config.yaml").read_bytes() == before
        assert terminal.prompts == 1
    finally:
        terminal.transcript.write_text(terminal.text().replace(password, "[REDACTED]"))
        terminal.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        vault.clear_vault_cache()
