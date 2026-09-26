"""Encrypted memory store: a vault frame log on disk, a LanceDB index in RAM.

The fork's invariant is that nothing readable lands inside a vaulted home, and LanceDB writes
plaintext Lance files. So the ONLY durable state is ``<home>/lancedb-memory/memories.jsonl``: an
append-only ``hermes_security.frames`` stream (AES-GCM per record, vault-derived key, flock'd
appends) of ``put``/``del`` ops. Text, metadata AND vectors are sealed — embeddings invert back to
text surprisingly well, so they are user content too. The LanceDB table lives in ``memory://``,
rebuilt from the decrypted log on open and brought forward incrementally: every read first
:meth:`refresh` es from the byte offset it last consumed, so a gateway, the CLI and the TUI sharing
one home all see each other's writes. A rewrite (compaction / erasure) changes the file's inode,
which forces other processes into a full reload.

``.jsonl`` + purpose ``transcript`` is the vault's convention for frame streams
(``hermes_security.migrate._frame_purpose``): ``secure-vault repair`` can re-frame this file.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
import uuid
import warnings
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import numpy as np

from .embedder import Embedder

logger = logging.getLogger(__name__)

DATA_DIR = "lancedb-memory"
LOG_NAME = "memories.jsonl"
FRAME_PURPOSE = "transcript"
KINDS = ("fact", "turn", "delegation")
_OP_VERSION = 1
_RRF_K = 60
# Auto-recall keeps hits scoring at least this fraction of the best hit's cosine similarity.
_RELATIVE_FLOOR = 0.5
# Compact once superseded/deleted frames exceed this share of the log (and the floor below).
_COMPACT_RATIO = 0.25
_COMPACT_MIN_DEAD = 200
# Re-index FTS after this many rows were added since the last build (unindexed rows are still
# searched, just by a flat scan).
_FTS_REINDEX_EVERY = 256
_META_COLUMNS = ["id", "kind", "text", "session_id", "platform", "source", "tags", "created_at", "updated_at"]


def _sql_str(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _encode_vector(vec: Optional[np.ndarray]) -> Optional[str]:
    return None if vec is None else base64.b64encode(np.asarray(vec, dtype="<f4").tobytes()).decode("ascii")


def _decode_vector(raw: Optional[str], dim: int) -> Optional[np.ndarray]:
    if not raw:
        return None
    vec = np.frombuffer(base64.b64decode(raw), dtype="<f4")
    return vec if vec.shape[0] == dim else None


def _op_id(payload: bytes) -> Optional[str]:
    try:
        op = json.loads(payload)
    except ValueError:
        return None
    return op.get("id") or (op.get("rec") or {}).get("id")


def compact_payloads(payloads: List[bytes], *, erase: Iterable[str] = ()) -> List[bytes]:
    """Keep only the latest ``put`` per live id; drop tombstones and every frame of *erase*."""
    erase = set(erase)
    latest: Dict[str, bytes] = {}
    for payload in payloads:
        try:
            op = json.loads(payload)
        except ValueError:
            continue
        if op.get("op") == "put":
            latest[op["rec"]["id"]] = payload
        elif op.get("op") == "del":
            latest.pop(op.get("id"), None)
    return [p for rid, p in latest.items() if rid not in erase]


class MemoryStore:
    """One per (home, embedding model) per process; shared by every agent on that profile."""

    def __init__(self, home: Path, embedder: Embedder, dim: int) -> None:
        self.home = home
        self.path = home / DATA_DIR / LOG_NAME
        self.embedder = embedder
        self.dim = dim
        self._lock = threading.RLock()
        self._records: Dict[str, Dict[str, Any]] = {}
        self._table = None
        self._offset = 0
        self._inode: Optional[int] = None
        self._frames_seen = 0
        self._fts_built_rows = -1
        self._rows_since_fts = 0
        self._backfilling = False

    # -- durable log -----------------------------------------------------------------

    def _vault(self):
        from hermes_security.vault import find_vault_home, get_vault

        return get_vault(find_vault_home(self.path))

    def _append(self, op: Dict[str, Any]) -> None:
        from hermes_security import frames

        op["v"] = _OP_VERSION
        frames.append(self.path, json.dumps(op, ensure_ascii=False).encode("utf-8"), purpose=FRAME_PURPOSE)

    def _rewrite(self, transform: Callable[[List[bytes]], List[bytes]]) -> None:
        from hermes_security import frames

        frames.rewrite(self.path, transform, purpose=FRAME_PURPOSE)
        with self._lock:
            self._reset()
            self.refresh()

    # -- in-memory index -------------------------------------------------------------

    def _reset(self) -> None:
        import lancedb
        import pyarrow as pa

        schema = pa.schema([
            ("id", pa.string()), ("kind", pa.string()), ("text", pa.string()),
            ("session_id", pa.string()), ("platform", pa.string()), ("source", pa.string()),
            ("tags", pa.list_(pa.string())), ("created_at", pa.float64()), ("updated_at", pa.float64()),
            ("has_vector", pa.bool_()), ("vector", pa.list_(pa.float32(), self.dim)),
        ])
        self._table = lancedb.connect("memory://").create_table("memories", schema=schema)
        self._records.clear()
        self._offset = 0
        self._inode = None
        self._frames_seen = 0
        self._fts_built_rows = -1
        self._rows_since_fts = 0

    def refresh(self) -> None:
        """Apply frames appended since the last read (by any process); full reload after a rewrite."""
        from hermes_security import frames

        with self._lock, frames.stream_lock(self.path):
            if self._table is None:
                self._reset()
            try:
                st = os.stat(self.path)
            except FileNotFoundError:
                if self._offset:
                    self._reset()
                return
            if self._inode not in (None, st.st_ino) or st.st_size < self._offset:
                self._reset()
            self._inode = st.st_ino
            if st.st_size == self._offset:
                return
            with open(self.path, "rb") as fh:
                fh.seek(self._offset)
                blob = fh.read()
            payloads = frames._complete_stream(blob, vault=self._vault(), purpose=FRAME_PURPOSE)
            self._offset += len(blob)
            self._frames_seen += len(payloads)
            self._apply(payloads)

    def _apply(self, payloads: List[bytes]) -> None:
        puts: Dict[str, Dict[str, Any]] = {}
        touched: set[str] = set()
        for payload in payloads:
            try:
                op = json.loads(payload)
            except ValueError:
                continue
            if op.get("op") == "put":
                rec = op["rec"]
                puts[rec["id"]] = rec
                touched.add(rec["id"])
            elif op.get("op") == "del":
                puts.pop(op.get("id"), None)
                touched.add(op.get("id"))
        if not touched:
            return
        existing = [rid for rid in touched if rid in self._records]
        if existing:
            self._table.delete("id IN (" + ", ".join(_sql_str(r) for r in existing) + ")")
        for rid in touched - puts.keys():
            self._records.pop(rid, None)
        rows = []
        for rid, rec in puts.items():
            vec = _decode_vector(rec.get("vector"), self.dim) if rec.get("embed_model") == self.embedder.model_name else None
            meta = {k: rec.get(k) for k in _META_COLUMNS}
            meta["tags"] = list(meta.get("tags") or [])
            meta["has_vector"] = vec is not None
            self._records[rid] = meta
            rows.append({**meta, "vector": vec if vec is not None else np.zeros(self.dim, dtype=np.float32)})
        if rows:
            self._table.add(rows)
            self._rows_since_fts += len(rows)

    def _ensure_fts(self) -> None:
        live = len(self._records)
        if not live or (self._fts_built_rows >= 0 and self._rows_since_fts < _FTS_REINDEX_EVERY):
            return
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # No stemming / stop words: memories are multilingual and English rules mangle the rest.
            self._table.create_fts_index("text", replace=True, stem=False, remove_stop_words=False, ascii_folding=True)
        self._fts_built_rows, self._rows_since_fts = live, 0

    # -- writes ----------------------------------------------------------------------

    def _embed(self, text: str) -> Optional[np.ndarray]:
        if not self.embedder.ready:
            return None
        try:
            return self.embedder.embed_documents([text])[0]
        except Exception as exc:
            logger.debug("LanceDB memory: embedding failed, storing keyword-only: %s", exc)
            return None

    def put(self, *, kind: str, text: str, session_id: str = "", platform: str = "", source: str = "",
            tags: Optional[List[str]] = None, record_id: str = "") -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            self.refresh()
            prior = self._records.get(record_id) if record_id else None
        rec = {
            "id": record_id or uuid.uuid4().hex[:16],
            "kind": kind, "text": text,
            "session_id": session_id if prior is None else prior["session_id"],
            "platform": platform if prior is None else prior["platform"],
            "source": source if prior is None else prior["source"],
            "tags": sorted({t.strip() for t in (tags if tags is not None else (prior or {}).get("tags") or []) if t.strip()}),
            "created_at": now if prior is None else prior["created_at"],
            "updated_at": now,
        }
        vec = self._embed(text)
        rec["embed_model"] = self.embedder.model_name if vec is not None else ""
        rec["vector"] = _encode_vector(vec)
        self._append({"op": "put", "rec": rec})
        self.refresh()
        self._maybe_compact()
        return self.get(rec["id"]) or rec

    def erase(self, record_ids: Iterable[str]) -> List[str]:
        """Delete AND scrub: the log is rewritten without any frame of these ids."""
        with self._lock:
            self.refresh()
            ids = [rid for rid in record_ids if rid in self._records]
        if ids:
            self._rewrite(lambda payloads: compact_payloads(payloads, erase=ids))
        return ids

    def _maybe_compact(self) -> None:
        with self._lock:
            dead = self._frames_seen - len(self._records)
            due = dead >= _COMPACT_MIN_DEAD and dead > _COMPACT_RATIO * self._frames_seen
        if due:
            self._rewrite(compact_payloads)

    def backfill_vectors(self, batch: int = 64) -> int:
        """Embed records stored keyword-only (model was still loading, or the model changed)."""
        with self._lock:
            if self._backfilling or not self.embedder.ready:
                return 0
            self._backfilling = True
        done = 0
        try:
            while True:
                with self._lock:
                    self.refresh()
                    todo = [dict(r) for r in self._records.values() if not r["has_vector"]][:batch]
                if not todo:
                    return done
                vectors = self.embedder.embed_documents([r["text"] for r in todo])
                for rec, vec in zip(todo, vectors):
                    rec.pop("has_vector", None)
                    rec.update(embed_model=self.embedder.model_name, vector=_encode_vector(vec))
                    self._append({"op": "put", "rec": rec})
                done += len(todo)
        finally:
            with self._lock:
                self._backfilling = False
            self._maybe_compact()

    # -- reads -----------------------------------------------------------------------

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self.refresh()
            rec = self._records.get(record_id)
            return dict(rec) if rec else None

    def records(self) -> List[Dict[str, Any]]:
        with self._lock:
            self.refresh()
            return [dict(r) for r in self._records.values()]

    def vector_of(self, record_id: str) -> Optional[np.ndarray]:
        with self._lock:
            self.refresh()
            if not (self._records.get(record_id) or {}).get("has_vector"):
                return None
            rows = self._table.search().where(f"id = {_sql_str(record_id)}").select(["vector"]).limit(1).to_list()
        return np.asarray(rows[0]["vector"], dtype=np.float32) if rows else None

    @staticmethod
    def build_where(*, kinds: Optional[List[str]] = None, session_id: str = "", exclude_session_id: str = "",
                    since: Optional[float] = None, until: Optional[float] = None,
                    tags: Optional[List[str]] = None, exclude_ids: Iterable[str] = ()) -> str:
        clauses = []
        if kinds:
            clauses.append("kind IN (" + ", ".join(_sql_str(k) for k in kinds) + ")")
        if session_id:
            clauses.append(f"session_id = {_sql_str(session_id)}")
        if exclude_session_id:
            # Only this session's TURNS are already in context; its facts are still worth recalling.
            clauses.append(f"NOT (kind = 'turn' AND session_id = {_sql_str(exclude_session_id)})")
        if since is not None:
            clauses.append(f"created_at >= {float(since)}")
        if until is not None:
            clauses.append(f"created_at < {float(until)}")
        for tag in tags or []:
            clauses.append(f"array_has(tags, {_sql_str(tag)})")
        exclude_ids = list(exclude_ids)
        if exclude_ids:
            clauses.append("id NOT IN (" + ", ".join(_sql_str(i) for i in exclude_ids) + ")")
        return " AND ".join(clauses)

    def vector_search(self, vector: np.ndarray, *, limit: int, where: str = "") -> List[Dict[str, Any]]:
        clause = "has_vector = true" + (f" AND ({where})" if where else "")
        with self._lock:
            self.refresh()
            if not self._records:
                return []
            rows = (self._table.search(vector, vector_column_name="vector").distance_type("cosine")
                    .where(clause, prefilter=True).limit(limit).select(_META_COLUMNS + ["_distance"]).to_list())
        for row in rows:
            row["similarity"] = round(1.0 - float(row.pop("_distance")), 4)
        return rows

    def keyword_search(self, query: str, *, limit: int, where: str = "") -> List[Dict[str, Any]]:
        with self._lock:
            self.refresh()
            if not self._records or not query.strip():
                return []
            self._ensure_fts()
            builder = self._table.search(query, query_type="fts").limit(limit).select(_META_COLUMNS + ["_score"])
            if where:
                builder = builder.where(where, prefilter=True)
            try:
                rows = builder.to_list()
            except Exception as exc:  # a query the FTS parser rejects must not sink the vector half
                logger.debug("LanceDB memory keyword search failed for %r: %s", query, exc)
                return []
        for row in rows:
            row["keyword_score"] = round(float(row.pop("_score")), 4)
        return rows

    def search(self, query: str, *, mode: str = "hybrid", limit: int = 8, where: str = "",
               min_similarity: Optional[float] = None) -> List[Dict[str, Any]]:
        """Hybrid = reciprocal-rank fusion of vector and BM25 results. ``min_similarity`` gates on
        cosine (auto-recall only): a keyword-only candidate has no similarity and is dropped then."""
        pool = max(limit * 3, 20)
        vector_rows: List[Dict[str, Any]] = []
        if mode in ("hybrid", "semantic") and self.embedder.ready:
            vector_rows = self.vector_search(self.embedder.embed_query(query), limit=pool, where=where)
        keyword_rows = self.keyword_search(query, limit=pool, where=where) if mode in ("hybrid", "keyword") else []
        fused: Dict[str, Dict[str, Any]] = {}
        for rows in (vector_rows, keyword_rows):
            for rank, row in enumerate(rows):
                entry = fused.setdefault(row["id"], {**row, "score": 0.0})
                entry.update({k: v for k, v in row.items() if k in ("similarity", "keyword_score")})
                entry["score"] += 1.0 / (_RRF_K + rank + 1)
        results = sorted(fused.values(), key=lambda r: r["score"], reverse=True)
        if min_similarity is not None:
            # Absolute floor, then relative: a 0.29 bystander rides along with a 0.72 hit otherwise.
            best = max((r.get("similarity", -1.0) for r in results), default=-1.0)
            floor = max(min_similarity, best * _RELATIVE_FLOOR)
            results = [r for r in results if r.get("similarity", -1.0) >= floor]
        for r in results:
            r["score"] = round(r["score"], 5)
        return results[:limit]


_STORES: Dict[tuple, MemoryStore] = {}
_STORES_LOCK = threading.Lock()


def get_store(home: Path, embedder: Embedder, dim: int) -> MemoryStore:
    key = (str(home.resolve()), embedder.model_name)
    with _STORES_LOCK:
        store = _STORES.get(key)
        if store is None:
            store = _STORES[key] = MemoryStore(home, embedder, dim)
    store.refresh()
    return store
