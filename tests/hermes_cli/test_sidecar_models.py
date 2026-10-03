"""Acquired model data is verified before publication; sidecars keep bounded KV contexts."""

import hashlib
import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from types import SimpleNamespace

import pytest

from hermes_cli.local_runtime.sidecar_models import MODELS, context_cap_for_model, download_verified
from pm.downloader import DownloadError


def test_download_is_atomic_verified_and_idempotent(tmp_path, monkeypatch):
    good = b"GGUF" + b"verified model data" * 10
    bodies = [b"x" * len(good), good]
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(good)))
            self.end_headers()

        def do_GET(self):
            probe = self.headers.get("Range") == "bytes=0-0"
            if not probe:
                requests.append(self.path)
            body = good if probe else bodies.pop(0)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    target = tmp_path / "model.gguf"
    target.write_bytes(b"previous complete file")
    try:
        kwargs = {"size_bytes": len(good), "sha256": hashlib.sha256(good).hexdigest()}
        url = f"http://127.0.0.1:{server.server_port}/model"
        with pytest.raises((ValueError, DownloadError), match="(?i)sha.?256"):
            download_verified(url, target, **kwargs)
        assert target.read_bytes() == b"previous complete file"
        assert not list(tmp_path.glob("*.part")) and not list(tmp_path.glob("*.verified"))
        assert download_verified(url, target, **kwargs).read_bytes() == good
        assert download_verified(url, target, **kwargs).read_bytes() == good
        assert len(requests) == 2
        # Publication also works when the cache and staged model directories
        # cannot share a hard link. The verified cache avoids another download.
        from hermes_cli.local_runtime import bootstrap, sidecar_models
        staged = tmp_path / "staged"
        staged.mkdir()
        monkeypatch.setattr(bootstrap, "assets_dir", lambda: tmp_path)
        monkeypatch.setattr(bootstrap, "models_dir", lambda: staged)
        artifact = SimpleNamespace(id="fixture", path=target.name, size_bytes=len(good), sha256=kwargs["sha256"],
                                   url=lambda _source: url, local_name=lambda role: role + ".gguf")
        monkeypatch.setitem(MODELS, "fixture", artifact)
        def different_mounts(_source, _target):
            raise OSError(errno.EXDEV, "Cross-device link")
        monkeypatch.setattr(sidecar_models.os, "link", different_mounts)
        assert sidecar_models.download_model("fixture", "guard") == "guard"
        assert (staged / "guard.gguf").read_bytes() == good
        assert sidecar_models.download_model("fixture", "guard") == "guard" and len(requests) == 2
        assert not list(staged.glob("*.link"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_role_context_caps_flow_into_the_real_launch_policy():
    from hermes_cli.local_runtime.context_policy import plan_launch, WindowDecision
    from hermes_cli.local_runtime.estimator import ModelProfile, LayerKind, HardwareBudget

    profile = ModelProfile(name="small", weights_bytes=512 << 20, embd_table_bytes=0,
                           n_ctx_train=262144, layers=[(LayerKind.FULL, 2048)] * 8)
    budget = HardwareBudget(usable_vram_bytes=4 << 30, total_device_bytes=4 << 30,
                            ram_available_bytes=16 << 30)
    for model in MODELS.values():
        assert len(model.hf_revision) == len(model.modelscope_revision) == 40
        assert len(model.sha256) == 64
        assert model.hf_revision in model.url("huggingface")
        assert model.modelscope_revision in model.url("modelscope")
        windows = []
        for role in ("guard", "router"):
            cap = context_cap_for_model(model.local_name(role).removesuffix(".gguf"))
            plan = plan_launch(profile, budget, context_cap=cap, requested_window=profile.n_ctx_train)
            assert isinstance(plan.decision, WindowDecision)
            assert plan.decision.window <= cap < profile.n_ctx_train
            windows.append(plan.decision.window)
        assert windows[0] < windows[1]
    assert context_cap_for_model("ordinary-main-model") is None
