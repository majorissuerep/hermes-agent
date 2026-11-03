"""Mistral context hints through its actual SDK and a synthetic loopback API.

No provider/model is contacted. The constructor seam selects only this local
transport; the real SDK method, signature, multipart encoder and parser run.
"""
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import wave

import pytest

mistral = pytest.importorskip("mistralai.client", reason="mistral extra required for actual SDK wire coverage")

_HINT = "Hermes, Teknium, Nous Research, kanban"
_KEY = "synthetic-mistral-wire-key"


@contextmanager
def _transcription_api():
    wire = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            message = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {self.headers['Content-Type']}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body)
            fields = {}
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                fields.setdefault(name, []).append(part.get_payload(decode=True))
            wire.append({"path": self.path, "fields": fields,
                         "authenticated": self.headers.get("Authorization") == f"Bearer {_KEY}"})
            payload = json.dumps({"model": "fixture-voxtral", "text": "wire transcript", "language": "en",
                                  "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", wire
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.mark.parametrize("with_hook", [True, False], ids=["hook-context-hint", "bare-request"])
def test_mistral_sdk_sends_hook_hint_as_native_context_bias(tmp_path, monkeypatch, caplog, with_hook):
    import httpx

    from hermes_security import io as state_io, vault
    from tests.e2e.vault_home import seal_fixture_home
    from tools import transcription_tools

    home = tmp_path / "owner"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    config = {"stt": {"provider": "mistral", "cloud_trim_silence": False,
                      "mistral": {"model": "fixture-voxtral", "language": "fr"}}}
    if with_hook:
        config["stt"]["prompt"] = "superseded static vocabulary"
    (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
    seal_fixture_home(home)
    state_io.write_text(home / ".env", f"MISTRAL_API_KEY={_KEY}\n", purpose="env")
    audio = tmp_path / "voice.wav"
    with wave.open(str(audio), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 160)

    hook_calls = []

    def hook(name, **kwargs):
        hook_calls.append((name, kwargs["provider"], kwargs["source"]))
        return [{"prompt": _HINT, "language": "en"}]

    monkeypatch.setattr("hermes_cli.plugins.has_hook", lambda name: with_hook and name == "pre_transcription")
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", hook)
    monkeypatch.setattr("pm.ensure_import", lambda *args, **kwargs: None)
    real_client = mistral.Mistral
    try:
        with _transcription_api() as (base, wire), httpx.Client(trust_env=False, timeout=5) as transport:
            monkeypatch.setattr(mistral, "Mistral", lambda **kwargs: real_client(
                **kwargs, server_url=base, client=transport))
            result = transcription_tools.transcribe_audio(str(audio), source="voice_mode")
        assert result["success"] and result["transcript"] == "wire transcript", result
        assert len(wire) == 1 and wire[0]["path"] == "/v1/audio/transcriptions"
        assert wire[0]["authenticated"]
        fields = wire[0]["fields"]
        assert fields["model"] == [b"fixture-voxtral"]
        assert fields["language"] == [b"en" if with_hook else b"fr"]
        assert "prompt" not in fields
        if with_hook:
            assert fields["context_bias"] == [_HINT.encode()]
            assert hook_calls == [("pre_transcription", "mistral", "voice_mode")]
        else:
            assert "context_bias" not in fields and not hook_calls
        assert (home / ".env").read_bytes().startswith(b"HRMVAULT\x00")
        assert _KEY not in caplog.text
    finally:
        vault.lock_now(home)
