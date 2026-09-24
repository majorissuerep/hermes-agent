"""LanceDB memory provider: real vault, real frame crypto, real in-memory LanceDB.

Only the embedding model is replaced (a deterministic bag-of-words hasher) so the suite never
downloads weights; everything between the provider API and the bytes on disk is the production path.
"""

from __future__ import annotations

import json
import re
import zlib

import numpy as np
import pytest

pytest.importorskip("lancedb")

from hermes_security import frames
from hermes_security import vault as hv
from plugins.memory.lancedb import LanceMemoryProvider
from plugins.memory.lancedb import embedder as emb
from plugins.memory.lancedb import store as st

DIM = 64
PASSWORD = "lancedb-memory-test-pw"


class HashEmbedder:
    """Unit-norm bag of lowercase words: shared words ⇒ positive cosine, disjoint ⇒ 0."""

    model_name = "test/hash-bow"
    error = ""

    def __init__(self, ready: bool = True) -> None:
        self._ready = ready

    @property
    def ready(self) -> bool:
        return self._ready

    def wait(self, timeout=None) -> bool:
        return self._ready

    def _vec(self, text: str) -> np.ndarray:
        vec = np.zeros(DIM, dtype=np.float32)
        for word in re.findall(r"\w+", text.lower()):
            vec[zlib.crc32(word.encode()) % DIM] += 1.0
        norm = np.linalg.norm(vec)
        return vec / norm if norm else vec

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


@pytest.fixture()
def vaulted_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    hv.init_vault(home, PASSWORD)
    hv.unlock(home, PASSWORD)
    monkeypatch.setattr(st, "_STORES", {})
    yield home
    hv.clear_vault_cache()


@pytest.fixture()
def hash_embedder(monkeypatch):
    shared = HashEmbedder()
    monkeypatch.setattr(emb, "get_embedder", lambda model, cache_dir: shared)
    monkeypatch.setattr(emb, "model_dim", lambda model: DIM)
    return shared


def _provider(home, session_id="s1", **config):
    provider = LanceMemoryProvider(config=None)
    provider.initialize(session_id, hermes_home=str(home), platform="cli")
    provider._config.update(config)
    return provider


def _call(provider, tool, **args):
    return json.loads(provider.handle_tool_call(tool, args))


def _log_payloads(home):
    return [json.loads(p) for p in frames.read_frames(home / st.DATA_DIR / st.LOG_NAME, purpose=st.FRAME_PURPOSE)]


def test_no_plaintext_reaches_disk(vaulted_home, hash_embedder):
    """Text, metadata and vectors are sealed; the LanceDB index never touches the home."""
    provider = _provider(vaulted_home)
    _call(provider, "lance_memory", action="remember", text="zebracorn-secret prefers ristretto", tags=["coffee"])
    provider.sync_turn("tell me about zebracorn-secret deployments", "zebracorn-secret deploys via wireguard",
                       session_id="s1")

    files = [p for p in vaulted_home.rglob("*") if p.is_file()]
    assert not [p for p in files if p.suffix == ".lance" or "_versions" in p.parts or "data" == p.parent.name]
    for path in files:
        assert b"zebracorn" not in path.read_bytes(), f"plaintext memory content in {path}"
    # ...yet the vault reads it back (canonical frame purpose, so `secure-vault repair` can too).
    kinds = sorted(op["rec"]["kind"] for op in _log_payloads(vaulted_home) if op["op"] == "put")
    assert kinds == ["fact", "turn"]


def test_forget_scrubs_every_frame_of_the_record(vaulted_home, hash_embedder):
    provider = _provider(vaulted_home)
    kept = _call(provider, "lance_memory", action="remember", text="keep this alpha fact")["memory"]["id"]
    gone = _call(provider, "lance_memory", action="remember", text="erase this omega fact")["memory"]["id"]
    _call(provider, "lance_memory", action="update", id=gone, text="erase this omega fact v2")

    assert _call(provider, "lance_memory", action="forget", id=gone)["erased"] == [gone]

    ids_on_disk = {op.get("id") or op["rec"]["id"] for op in _log_payloads(vaulted_home)}
    assert ids_on_disk == {kept}
    assert gone not in {r["id"] for r in _call(provider, "lance_recall", query="omega fact")["results"]}


def test_second_process_sees_writes_and_erasures(vaulted_home, hash_embedder):
    """Two stores on one home (gateway + CLI): appends apply incrementally, a rewrite forces reload."""
    a = st.MemoryStore(vaulted_home, hash_embedder, DIM)
    b = st.MemoryStore(vaulted_home, hash_embedder, DIM)
    rec = a.put(kind="fact", text="shared wireguard endpoint on port 51820")
    assert [r["id"] for r in b.search("wireguard endpoint", limit=3)] == [rec["id"]]
    other = b.put(kind="fact", text="second fact about tailscale")
    assert a.get(other["id"]) is not None
    a.erase([rec["id"]])
    assert b.get(rec["id"]) is None
    assert b.get(other["id"]) is not None


def test_recall_skips_current_session_turns_and_unrelated_prompts(vaulted_home, hash_embedder):
    old = _provider(vaulted_home, session_id="old")
    old.sync_turn("how do we rotate the grafana api token", "rotate the grafana token via vault kv put",
                  session_id="old")

    assert old.prefetch("rotate the grafana token", session_id="old") == ""  # already in context
    fresh = _provider(vaulted_home, session_id="new")
    block = fresh.prefetch("rotate the grafana token", session_id="new")
    assert "grafana" in block and fresh.recall_status().count == 1
    assert fresh.prefetch("write a haiku about oceans", session_id="new") == ""
    assert fresh.recall_status() is None


def test_recall_drops_weak_bystanders_of_a_strong_hit(vaulted_home, hash_embedder):
    old = _provider(vaulted_home, session_id="old")
    old.sync_turn("rotate the grafana api token", "rotate the grafana token via vault kv put", session_id="old")
    old.sync_turn("lunch order for friday", "the token for pizza is blue", session_id="old")
    fresh = _provider(vaulted_home, session_id="new", recall_min_similarity=0.0)

    block = fresh.prefetch("rotate the grafana token", session_id="new")
    assert "grafana" in block and "pizza" not in block


def test_navigation_walks_sessions_turns_and_neighbours(vaulted_home, hash_embedder):
    provider = _provider(vaulted_home, session_id="s1")
    for i in range(3):
        provider.sync_turn(f"question {i} about kubernetes ingress", f"answer {i} about ingress", session_id="s1")
    provider.sync_turn("unrelated chat about lunch", "pasta sounds great", session_id="s2")

    sessions = _call(provider, "lance_memory", action="sessions")["sessions"]
    assert {s["session_id"]: s["turns"] for s in sessions} == {"s1": 3, "s2": 1}
    assert next(s for s in sessions if s["session_id"] == "s1")["current"] is True

    replay = _call(provider, "lance_memory", action="session", session_id="s1")["memories"]
    assert [m["text"].split(" about")[0] for m in replay] == ["User: question 0", "User: question 1", "User: question 2"]

    middle = _call(provider, "lance_memory", action="get", id=replay[1]["id"])
    assert (middle["previous_turn"]["id"], middle["next_turn"]["id"]) == (replay[0]["id"], replay[2]["id"])

    page = _call(provider, "lance_memory", action="timeline", limit=2)
    assert page["count"] == 2 and page["total"] == 4 and page["next_offset"] == 2
    related = _call(provider, "lance_memory", action="related", id=replay[0]["id"])["results"]
    assert replay[0]["id"] not in {r["id"] for r in related}


def test_profiles_are_isolated(tmp_path, monkeypatch, hash_embedder):
    """A → B → A: each home's memories stay in that home's vault."""
    monkeypatch.setattr(st, "_STORES", {})
    homes = {}
    for name in ("a", "b"):
        homes[name] = tmp_path / name / ".hermes"
        hv.init_vault(homes[name], PASSWORD + name)
        hv.unlock(homes[name], PASSWORD + name)
    try:
        pa = _provider(homes["a"])
        _call(pa, "lance_memory", action="remember", text="profile alpha owns the staging cluster")
        pb = _provider(homes["b"])
        assert _call(pb, "lance_recall", query="staging cluster")["count"] == 0
        _call(pb, "lance_memory", action="remember", text="profile beta owns the billing service")
        pa_again = _provider(homes["a"])
        texts = [r["text"] for r in _call(pa_again, "lance_recall", query="owns")["results"]]
        assert texts == ["profile alpha owns the staging cluster"]
    finally:
        hv.clear_vault_cache()


def test_keyword_only_records_are_embedded_once_the_model_loads(vaulted_home, monkeypatch):
    cold = HashEmbedder(ready=False)
    store = st.MemoryStore(vaulted_home, cold, DIM)
    rec = store.put(kind="fact", text="stored while the model was loading: nebula")
    assert store.get(rec["id"])["has_vector"] is False
    assert [r["id"] for r in store.search("nebula", mode="keyword", limit=3)] == [rec["id"]]

    cold._ready = True
    assert store.backfill_vectors() == 1
    assert [r["id"] for r in store.search("nebula model", mode="semantic", limit=3)] == [rec["id"]]


def test_builtin_memory_writes_are_mirrored(vaulted_home, hash_embedder):
    provider = _provider(vaulted_home)
    provider.on_memory_write("add", "user", "Name is Sam; timezone Europe/Kyiv")
    provider.on_memory_write("replace", "user", "Name is Sam; timezone Europe/Warsaw",
                             metadata={"old_text": "timezone Europe/Kyiv"})
    facts = _call(provider, "lance_memory", action="timeline", kinds=["fact"])["memories"]
    assert [f["text"] for f in facts] == ["Name is Sam; timezone Europe/Warsaw"]
    provider.on_memory_write("remove", "user", "", metadata={"old_text": "Europe/Warsaw"})
    assert _call(provider, "lance_memory", action="stats")["total"] == 0


def test_unavailable_without_a_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    (tmp_path / ".hermes").mkdir()
    assert LanceMemoryProvider().is_available() is False
