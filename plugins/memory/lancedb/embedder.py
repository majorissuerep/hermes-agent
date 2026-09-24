"""Local text embeddings (fastembed / ONNX) — memory content never leaves the machine.

One model instance per (model, cache dir) is shared by every store in the process. Weights are
public artifacts cached under ``<HERMES_HOME>/cache/fastembed`` (a vault skip dir) and fetched from
Hugging Face once on first use; loading runs on a background thread so a cold start (or an
offline first run) never blocks a turn — callers check :attr:`Embedder.ready` and degrade.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from agent.memory_provider import spawn_context_thread

logger = logging.getLogger(__name__)

# Multilingual (~50 languages) and the only candidate that ranked every probe correctly, including
# Russian queries against English memories; 384-d keeps each encrypted record small.
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class Embedder:
    def __init__(self, model_name: str, cache_dir: Path) -> None:
        self.model_name = model_name
        self._cache_dir = cache_dir
        self._model = None
        self._dim: Optional[int] = None
        self._error = ""
        self._ready = threading.Event()
        self._started = False
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and self._model is not None

    @property
    def error(self) -> str:
        return self._error

    @property
    def dim(self) -> Optional[int]:
        return self._dim

    def warm(self) -> None:
        """Start loading on a background thread (idempotent)."""
        with self._lock:
            if self._started:
                return
            self._started = True
        spawn_context_thread(self._load, name="lancedb-memory-embedder").start()

    def wait(self, timeout: Optional[float] = None) -> bool:
        self.warm()
        self._ready.wait(timeout)
        return self.ready

    def _load(self) -> None:
        try:
            from fastembed import TextEmbedding

            self._cache_dir.mkdir(parents=True, exist_ok=True)
            model = TextEmbedding(self.model_name, cache_dir=str(self._cache_dir))
            probe = next(iter(model.embed(["probe"])))
            self._dim = int(probe.shape[0])
            self._model = model
        except Exception as exc:  # offline first run, unknown model, broken wheel
            self._error = f"{type(exc).__name__}: {exc}"
            logger.warning("LanceDB memory: embedding model %s unavailable (%s); search falls back to keywords",
                           self.model_name, self._error)
        finally:
            self._ready.set()

    def _normalize(self, vectors) -> List[np.ndarray]:
        out = []
        for vec in vectors:
            vec = np.asarray(vec, dtype=np.float32)
            norm = float(np.linalg.norm(vec))
            out.append(vec / norm if norm else vec)
        return out

    def embed_documents(self, texts: List[str]) -> List[np.ndarray]:
        if not self.ready:
            raise RuntimeError(f"embedding model not ready: {self._error or 'loading'}")
        return self._normalize(self._model.embed(texts))

    def embed_query(self, text: str) -> np.ndarray:
        if not self.ready:
            raise RuntimeError(f"embedding model not ready: {self._error or 'loading'}")
        return self._normalize(self._model.query_embed([text]))[0]


def model_dim(model_name: str) -> int:
    """Vector width from fastembed's catalog, without loading weights: the index schema must exist
    before the (background) model load finishes. Unknown names raise — fastembed can't load them."""
    from fastembed import TextEmbedding

    for spec in TextEmbedding.list_supported_models():
        if spec["model"] == model_name:
            return int(spec["dim"])
    raise ValueError(f"unknown fastembed model {model_name!r}")


_EMBEDDERS: Dict[Tuple[str, str], Embedder] = {}
_EMBEDDERS_LOCK = threading.Lock()


def get_embedder(model_name: str, cache_dir: Path) -> Embedder:
    key = (model_name, str(cache_dir))
    with _EMBEDDERS_LOCK:
        embedder = _EMBEDDERS.get(key)
        if embedder is None:
            embedder = _EMBEDDERS[key] = Embedder(model_name, cache_dir)
    embedder.warm()
    return embedder
