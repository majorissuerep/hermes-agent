"""Real Ink keyboard model controls must change the local agent route and retain history."""
from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import secrets
import select
import shutil
import struct
import subprocess
import sys
import threading
import time
from collections import deque

import pytest

from tests.waiting import wait_until
from tests.e2e.core.terminal._vt import Screen

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.platforms("linux", "macos"), pytest.mark.live_system_guard_bypass]


class ModelWire(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self._json({"object": "list", "data": [
            {"id": model, "object": "model", "owned_by": "fixture"}
            for model in ("fixture-alpha", "fixture-beta", "fixture-gamma")]})

    def _json(self, body, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if status == 429:
            self.send_header("Retry-After", "0")
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
        if errors := getattr(self.server, "response_errors", []):
            status = errors.pop(0)
            self._json({"error": {"message": f"fixture-provider-refusal-{status}",
                                  "type": "rate_limit_error" if status == 429 else "authentication_error"}}, status)
            return
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


def save_fixture_runtime_logs(home, password, output):
    """After owner exit, retain bounded authenticated synthetic diagnostics, never its password/key."""
    from hermes_security import frames, vault
    from hermes_security.errors import VaultError

    vault.unlock(home, password)
    notes = []
    for path in sorted((home / "logs").rglob("*.log"))[:8]:
        try:
            payload = b"".join(deque(frames.read_frames(path, purpose="log"), maxlen=100))
            text = payload.decode("utf-8", errors="replace")[-12000:]
        except (OSError, VaultError) as error:
            text = f"diagnostic read failed: {type(error).__name__}"
        for value in (password, "synthetic-fixture-key", "synthetic-global-key"):
            text = text.replace(value, "[REDACTED]")
        notes.append(f"--- {path.relative_to(home)} ---\n{text}")
    output.write_text("\n".join(notes))


class TerminalPTY:
    def __init__(self, env, password, transcript, *, args=(), argv=None, project=None, timeout=45, sensitive_values=()):
        import fcntl
        import pty
        import termios

        self.password = password
        self.timeout = timeout
        self.sensitive_values = (password, *sensitive_values)
        self.transcript = transcript
        self.prompts = 0
        self.output = bytearray()
        self.started_at = time.monotonic()
        self.read_metrics = {"chunks": 0, "parse_s": 0.0, "write_s": 0.0,
                             "max_parse_s": 0.0, "max_write_s": 0.0}
        self.screen = Screen(42, 140)
        project = Path(project) if project is not None else Path(env["HOME"]) / "foreign-project"
        project.mkdir(parents=True, exist_ok=True)
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 42, 140, 0, 0))
        self.process = subprocess.Popen(argv or [sys.executable, "-m", "hermes_cli.main", *args],
            stdin=slave, stdout=slave, stderr=slave, cwd=project,
            env={**env, "TERM": "xterm-256color"}, start_new_session=True,
            preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))  # noqa: PLW1509 -- child requires its controlling PTY
        os.close(slave)
        from hermes_platform.processes import get_process_start_time
        self.owner_start = get_process_start_time(self.process.pid)
        self.owner_session = os.getsid(self.process.pid)
        self.owner_group = os.getpgid(self.process.pid)
        assert self.owner_start is not None and self.owner_session == self.process.pid
        self.closed = False

    def read(self):
        if select.select([self.master], [], [], 0.1)[0]:
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                chunk = b""
            self.output.extend(chunk)
            started = time.monotonic()
            self.screen.feed(chunk)
            parsed = time.monotonic() - started
            self.read_metrics["chunks"] += 1
            self.read_metrics["parse_s"] += parsed
            self.read_metrics["max_parse_s"] = max(self.read_metrics["max_parse_s"], parsed)
            if b"\x1b[6n" in chunk:
                os.write(self.master, b"\x1b[1;1R")
            if self.prompts == 0 and b"Unlock master password:" in self.output:
                self.prompts += 1
                os.write(self.master, self.password.encode() + b"\n")
            started = time.monotonic()
            self.transcript.write_text(self.text())
            written = time.monotonic() - started
            self.read_metrics["write_s"] += written
            self.read_metrics["max_write_s"] = max(self.read_metrics["max_write_s"], written)
        return self.text()

    def text(self):
        text = "\n".join(self.screen.history + self.screen.display())
        for value in self.sensitive_values:
            if value:
                text = text.replace(value, "[REDACTED]")
        return text

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
            return wait_until(ready, timeout=self.timeout, what=description)
        except AssertionError as error:
            raise AssertionError(str(error) + "\n" + self.text()[-6000:]) from error

    def submit(self, text, *, rendered_suffix=None):
        os.write(self.master, text.encode())
        # Model actual separate keypresses: <50ms text+Enter is intentionally treated as paste.
        if rendered_suffix is None:
            self.until(lambda output: " ".join(text.split()) in " ".join(output.split()), "typed text rendered")
        else:
            expected = "".join(rendered_suffix.split())
            self.until(lambda _output: expected in "".join("".join(self.screen.display()).split()),
                       "current input suffix rendered")
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
        import psutil
        from hermes_platform.processes import get_process_start_time, pid_exists, terminate_pid

        if self.closed:
            return
        self.closed = True
        deadline = time.monotonic() + 10
        owned = {}
        try:
            current = get_process_start_time(self.process.pid)
            assert current is None or current == self.owner_start, "PTY owner PID was reused"
            # The root can move groups; a group signal alone does not prove its exit.
            if self.process.poll() is None:
                current = get_process_start_time(self.process.pid)
                assert current is None or current == self.owner_start, "PTY owner PID was reused"
                try:
                    children = psutil.Process(self.process.pid).children(recursive=True)
                except psutil.NoSuchProcess:
                    children = []
                for child in children:
                    owned[child.pid] = get_process_start_time(child.pid)
                if current is not None:
                    owned[self.process.pid] = self.owner_start
            for candidate in psutil.process_iter(["pid", "uids"]):
                if candidate.info["uids"] is None or candidate.info["uids"].real != os.getuid():
                    continue
                try:
                    if os.getsid(candidate.pid) == self.owner_session:
                        owned[candidate.pid] = get_process_start_time(candidate.pid)
                except ProcessLookupError:
                    continue
            for pid, started in owned.items():
                if pid_exists(pid):
                    assert started is not None, f"PTY child {pid} has no start identity"
                    try:
                        terminate_pid(pid, force=True, expected_start_time=started)
                    except ProcessLookupError:
                        continue
                    except OSError as error:
                        if isinstance(error, PermissionError) or pid_exists(pid):
                            raise
            self.process.wait(timeout=max(0, deadline - time.monotonic()))
            wait_until(lambda: all(not pid_exists(pid) or get_process_start_time(pid) != started
                for pid, started in owned.items()), timeout=max(0, deadline - time.monotonic()),
                what="owned PTY descendants terminated")
        finally:
            os.close(self.master)
            self._save_transcript()

    def _save_transcript(self):
        raw = self.output.decode("utf-8", errors="replace")
        for value in self.sensitive_values:
            if value:
                raw = raw.replace(value, "[REDACTED]")
        self.transcript.with_suffix(".ansi.log").write_text(raw)
        self.transcript.with_suffix(".metrics.json").write_text(json.dumps({
            **self.read_metrics, "elapsed_s": time.monotonic() - self.started_at,
            "bytes": len(self.output), "unlock_prompts": self.prompts,
            "returncode": self.process.returncode}, indent=2))


def _composer_ready(terminal):
    rows = terminal.screen.display()
    current = "\n".join(rows)
    composer = "\n".join(rows[-12:])
    return ("─ ready │" in composer and "❯" in composer
            and "Esc/q close" not in current and "Config File:" not in current)


def _exercise_failed_model_controls(terminal, server):
    """Rejected controls preserve the route/cache; transient and auth faults differ."""
    count = len(server.requests)
    prefix = server.requests[-1]["messages"]
    terminal.submit("/model")
    terminal.until(lambda _: "Select provider (step 1/3)" in "\n".join(terminal.screen.display()),
                   "cancelable provider picker")
    terminal.key(b"\x1b")
    terminal.until(lambda _: "Select provider (step 1/3)" not in "\n".join(terminal.screen.display())
                   and "─ ready │" in "\n".join(terminal.screen.display()), "picker canceled")
    assert len(server.requests) == count
    terminal.submit("/model fixture-beta --provider fixture-missing --session")
    terminal.until(lambda text: "Unknown provider 'fixture-missing'" in text, "invalid route refused")
    assert len(server.requests) == count
    terminal.submit("after-rejected-model-controls")
    terminal.until(lambda text: f"fixture-reply-{count + 1}" in text, "working route after refusal")
    assert server.requests[-1]["model"] == "fixture-gamma"
    assert server.requests[-1]["messages"][:len(prefix)] == prefix

    count = len(server.requests)
    server.response_errors = [429]
    terminal.submit("recover-one-transient-rate-limit")
    terminal.until(lambda text: f"fixture-reply-{count + 2}" in text, "bounded rate-limit recovery")
    assert len(server.requests) == count + 2
    assert [row["model"] for row in server.requests[-2:]] == ["fixture-gamma", "fixture-gamma"]
    assert server.requests[-2]["messages"] == server.requests[-1]["messages"]

    count = len(server.requests)
    server.response_errors = [401] * 4
    terminal.submit("refuse-invalid-credentials")
    terminal.until(lambda text: "fixture-provider-refusal-401" in text
                   and "─ ready │" in "\n".join(terminal.screen.display()), "authentication refusal settled")
    assert len(server.requests) == count + 1, "authentication failure must not retry like a transient 429"
    server.response_errors = []
    terminal.submit("after-explicit-auth-repair")
    terminal.until(lambda text: f"fixture-reply-{count + 2}" in text, "working route after credential repair")
    assert server.requests[-1]["model"] == "fixture-gamma"
    assert server.requests[-1]["messages"][:len(prefix)] == prefix


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
    from tests.e2e.vault_home import prepare_fixture_tui

    prepare_fixture_tui(home, ROOT)
    vault.clear_vault_cache()
    from hermes_cli._launchers import runtime_command

    env = dict(os.environ)
    env.pop("HERMES_PYTHON", None)
    # Exercise the real installation-bound store interpreter, not the dependency-bearing
    # test interpreter that masks missing activation in Node's private Python child.
    terminal = TerminalPTY(env, password, tmp_path / "terminal-model-transcript.log",
                           argv=runtime_command(ROOT))
    anchor = "history-" + secrets.token_hex(10)
    try:
        terminal.until(lambda _: _composer_ready(terminal), "Ink composer ready")
        before = (home / "config.yaml").read_bytes()
        terminal.submit("/config")
        terminal.until(lambda _: "(^_^) Configuration" in "\n".join(terminal.screen.display())
                       and "Esc/q close" in "\n".join(terminal.screen.display()),
                       "complete config pager ready for keyboard input")
        terminal.key(b"q")
        terminal.until(lambda _: _composer_ready(terminal), "config pager closed; composer ready")
        if not lazy:
            terminal.submit(anchor)
            terminal.until(lambda text: "fixture-reply-1" in text and _composer_ready(terminal),
                           "first local agent reply; composer ready")
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
        terminal.until(lambda _: any("─ ready │" in row and " high │" in row
                                     for row in terminal.screen.display()), "one-turn high effort footer")
        for offset in (2, 3):
            terminal.submit(f"once-check-{offset}")
            terminal.until(lambda text: f"fixture-reply-{count + offset}" in text, "one-turn route reply")
        assert [request["model"] for request in server.requests][-2:] == ["fixture-beta", "fixture-gamma"]
        assert server.requests[-2].get("reasoning_effort") == "high"
        assert server.requests[-1].get("reasoning_effort") in (None, "none")
        pivots = [str(row.get("content")) for row in server.requests[-1]["messages"]
                  if row["role"] == "user" and str(row.get("content")).startswith("[System: The active model")]
        assert "fixture-gamma" in pivots[-1], pivots
        if not lazy:
            _exercise_failed_model_controls(terminal, server)
        first_system = server.requests[0]["messages"][0]
        assert first_system["role"] == "system"
        assert all(request["messages"][0] == first_system for request in server.requests)
        assert (home / "config.yaml").read_bytes() == before
        assert terminal.prompts == 1
        terminal.until(lambda _: _composer_ready(terminal), "completed model controls; composer ready")
        terminal.submit("/quit")
        terminal.until(lambda _: terminal.process.poll() is not None, "public model-control owner exited")
        assert terminal.process.returncode == 0
    finally:
        terminal.transcript.write_text(terminal.text().replace(password, "[REDACTED]"))
        terminal.close()
        save_fixture_runtime_logs(home, password, tmp_path / "runtime-diagnostics.log")
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        vault.clear_vault_cache()
