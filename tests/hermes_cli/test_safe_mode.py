"""Real terminal headless safe-mode bypasses user configuration and contextual files."""
from hermes_cli.config_defaults import DEFAULT_CONFIG

from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
import time

import pytest



class Model(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if body.get('messages'):
            self.server.requests.append({'auth': self.headers.get('Authorization'), 'path': self.path, 'body': body})
        message = {'role': 'assistant', 'content': 'SAFE_REPLY_OK'}
        choice = {'index': 0, 'message': message, 'finish_reason': 'stop'}
        frame = {'id': 'local', 'model': body.get('model'), 'choices': [choice],
                 'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}
        kind = 'application/json'
        if body.get('stream'):
            choice['delta'] = choice.pop('message')
            payload = ('data: ' + json.dumps(frame) + '\n\ndata: [DONE]\n\n').encode()
            kind = 'text/event-stream'
        else:
            payload = json.dumps(frame).encode()
        try:
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass


def _pty_cli(argv, cwd, env, timeout=90):
    """Real PTY launch: stdin/stdout/stderr are a terminal, like a user's shell."""
    import pty  # POSIX-only; the module must still collect on Windows
    master, slave = pty.openpty()
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=slave, stdout=slave, stderr=slave, close_fds=True, start_new_session=True)
    os.close(slave)
    output, deadline = bytearray(), time.monotonic() + timeout
    with closing(os.fdopen(master, 'rb', buffering=0)) as reader:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([reader], [], [], .2)
            if ready:
                try:
                    chunk = reader.read(65536)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
            elif proc.poll() is not None:
                break
    try:
        code = proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        raise AssertionError("CLI timed out: " + output.decode("utf-8", "replace"))
    return code, output.decode('utf-8', 'replace')


@pytest.mark.platforms("linux")
def test_safe_headless_launch_skips_user_context_and_survives_invalid_yaml(tmp_path):
    root = Path(__file__).resolve().parents[2]
    home, user, work = (tmp_path / name for name in ("state", "user", "work"))
    for directory in (home, user, work):
        directory.mkdir()
    sentinel = home / "plugin-loaded"
    plugin = home / "plugins" / "sentinel"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text("name: sentinel\nversion: 1.0.0\nkind: standalone\n")
    (plugin / "__init__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).touch()\n"
        "def register(ctx): pass\n")
    (home / "SOUL.md").write_text("SOUL_SENTINEL")
    (work / "AGENTS.md").write_text("RULES_SENTINEL")
    peer = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    peer.requests = []
    thread = threading.Thread(target=peer.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{peer.server_port}/v1"
    config = {"_config_version": DEFAULT_CONFIG["_config_version"],
              "model": {"provider": "custom", "default": "fixture", "base_url": url},
              "plugins": {"enabled": ["sentinel"]},
              "memory": {"memory_enabled": False, "user_profile_enabled": False},
              "auxiliary": {"title_generation": {"enabled": False}},
              "platform_toolsets": {"cli": []}}
    config_path = home / "config.yaml"
    config_path.write_text(json.dumps(config))
    env = {key: os.environ[key] for key in ("PATH", "LANG", "TZ", "TIRITH_ENABLED") if key in os.environ}
    env.update(HERMES_ALLOW_NO_VAULT="1", HOME=str(user), USERPROFILE=str(user),
               HERMES_HOME=str(home), PYTHONPATH=str(root), TERM="xterm",
               OPENAI_API_KEY="loopback-only", OPENAI_BASE_URL=url)

    def run(tag, *flags):
        code, output = _pty_cli([sys.executable, "-m", "hermes_cli.main", "chat", *flags,
            "--provider", "custom", "--base-url", url, "--model", "fixture", "--api-key", "fixture",
            "-Q", "-q", tag], cwd=work, env=env)
        assert code == 0 and "SAFE_REPLY_OK" in output, output
        matches = [request for request in peer.requests if request["body"].get("messages", [])[-1].get("content") == tag]
        assert len(matches) == 1, peer.requests
        return json.dumps(matches[0]["body"]["messages"])

    try:
        ordinary = run("ORDINARY_PROBE")
        assert sentinel.exists(), "positive control must load the enabled plugin"
        assert "SOUL_SENTINEL" in ordinary and "RULES_SENTINEL" in ordinary
        sentinel.unlink()
        safe = run("SAFE_PROBE", "--safe-mode")
        assert not sentinel.exists()
        assert "SOUL_SENTINEL" not in safe and "RULES_SENTINEL" not in safe
        config_path.write_text("model: [unterminated\n")
        before = config_path.read_bytes()
        run("SAFE_INVALID_CONFIG", "--safe-mode")
        assert config_path.read_bytes() == before
        assert not sentinel.exists()
    finally:
        peer.shutdown()
        peer.server_close()
        thread.join(timeout=5)
