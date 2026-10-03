"""Pinned small models for optional local tool review and request routing.

The Hugging Face and ModelScope copies below have identical SHA-256 digests.
Only model data is acquired; remote Python and repository code are never loaded.
"""

from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import urllib.parse


@dataclass(frozen=True)
class SidecarModel:
    id: str
    repo: str
    path: str
    hf_revision: str
    modelscope_revision: str
    sha256: str
    size_bytes: int
    parameters: str

    def url(self, source: str) -> str:
        if source == "huggingface":
            return f"https://huggingface.co/{self.repo}/resolve/{self.hf_revision}/{self.path}"
        if source == "modelscope":
            query = urllib.parse.urlencode({"Revision": self.modelscope_revision, "FilePath": self.path})
            return f"https://modelscope.cn/api/v1/models/{self.repo}/repo?{query}"
        raise ValueError("source must be huggingface or modelscope")

    def local_name(self, role: str) -> str:
        if role not in {"guard", "router"}:
            raise ValueError("role must be guard or router")
        return f"hermes-sidecar-{role}-{self.id}.gguf"


MODELS = {
    model.id: model for model in (
        SidecarModel(
            "qwen3.5-0.8b", "unsloth/Qwen3.5-0.8B-GGUF", "Qwen3.5-0.8B-Q4_K_M.gguf",
            "6ab461498e2023f6e3c1baea90a8f0fe38ab64d0", "88467eb7c8e3b6e7894c794f373050d4dbc6ae8a",
            "bd258782e35f7f458f8aced1adc053e6e92e89bc735ba3be89d38a06121dc517", 532517120, "0.8B",
        ),
        SidecarModel(
            "qwen3.5-2b", "unsloth/Qwen3.5-2B-GGUF", "Qwen3.5-2B-Q4_K_M.gguf",
            "f6d5376be1edb4d416d56da11e5397a961aca8ae", "90057e31161eb95cc0bc1413c4f53b44de9b49c8",
            "aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223", 1280835840, "2B",
        ),
        SidecarModel(
            "qwen3.5-4b", "unsloth/Qwen3.5-4B-GGUF", "Qwen3.5-4B-Q4_K_M.gguf",
            "e87f176479d0855a907a41277aca2f8ee7a09523", "167b4afc359863325cb4164418c715421b4e9118",
            "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4", 2740937888, "4B",
        ),
    )
}


def context_cap_for_model(model_id: str) -> int | None:
    # Distinct role ids isolate their KV context from a main agent using the same weights.
    for role, cap in (("guard", 8192), ("router", 24576)):
        if any(model_id == Path(model.local_name(role)).stem for model in MODELS.values()):
            return cap
    return None


def _verified(path: Path, size: int, digest: str) -> bool:
    if not path.is_file() or path.stat().st_size != size:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == digest


def download_verified(url: str, destination: Path, *, size_bytes: int, sha256: str, progress=None) -> Path:
    from pm.downloader import Download, Source, replace_when_released
    if _verified(destination, size_bytes, sha256):
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination.parent).free < size_bytes:
        raise OSError(f"Download needs {size_bytes / (1 << 30):.2f} GiB of free storage")
    fd, temporary = tempfile.mkstemp(prefix=destination.name + ".", suffix=".verified", dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary)
    try:
        def bounded_progress(done, _total, _ranges):
            if done > size_bytes:
                raise ValueError("Model download exceeds its pinned size")
            if progress:
                progress(done, size_bytes)

        Download([Source(url, temporary, sha256)], resume=False, connections=1).run(progress=bounded_progress)
        if not _verified(temporary, size_bytes, sha256):
            raise ValueError("Model download failed size or SHA-256 verification")
        replace_when_released(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def download_model(model_id: str, role: str, source: str = "huggingface", *, progress=None) -> str:
    from hermes_cli.local_runtime.bootstrap import assets_dir, models_dir
    model = MODELS[model_id]
    target = models_dir() / model.local_name(role)
    cached = assets_dir() / model.path
    download_verified(model.url(source), cached, size_bytes=model.size_bytes, sha256=model.sha256, progress=progress)
    if not _verified(target, model.size_bytes, model.sha256):
        # Link the verified cache atomically; a running router never sees a partial GGUF.
        from pm.downloader import replace_when_released
        fd, link = tempfile.mkstemp(prefix=target.name + ".", suffix=".link", dir=target.parent)
        os.close(fd)
        temporary = Path(link)
        temporary.unlink()
        try:
            try:
                os.link(cached, temporary)
            except OSError as exc:
                if exc.errno not in {errno.EXDEV, errno.EPERM, errno.ENOTSUP}:
                    raise
                # Separate mounts and filesystems without hard links still support setup.
                shutil.copyfile(cached, temporary)
            replace_when_released(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return target.stem
