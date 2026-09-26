"""Real-process encrypted memory visibility and interrupted-write qualification."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import cast
from plugins.memory.lancedb.embedder import Embedder

import pytest

pytest.importorskip("lancedb")
from hermes_security import vault
from hermes_security.errors import VaultIntegrityError
from plugins.memory.lancedb.store import MemoryStore

PASSWORD = "synthetic-process-memory"
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def home(tmp_path):
    path = tmp_path / "home"
    vault.init_vault(path, PASSWORD)
    vault.unlock(path, PASSWORD)
    try:
        yield path
    finally:
        vault.clear_vault_cache()


def _store(home):
    return MemoryStore(home, cast(Embedder, SimpleNamespace(ready=False, model_name="test/no-embedding")), 8)


def test_memory_put_never_acknowledges_hidden_record(home):
    store = _store(home)
    first = store.put(kind="fact", text="before interruption")
    with store.path.open("ab") as handle:
        handle.write(b"\x00\x00\x10\x00partial")
    damaged = store.path.read_bytes()
    try:
        after = store.put(kind="fact", text="after interruption")
    except VaultIntegrityError:
        assert store.path.read_bytes() == damaged
    else:
        assert _store(home).get(after["id"]) is not None
    with pytest.raises(VaultIntegrityError):
        _store(home).get(first["id"])
    # Prefix recovery remains explicit for log display/repair, not MemoryStore reads.
    from hermes_security import frames
    recovered = [json.loads(raw) for raw in frames.read_frames(store.path, purpose="transcript")]
    assert recovered[0]["rec"]["id"] == first["id"]


def test_fresh_process_observes_writes_and_erasures(home):
    store = _store(home)
    first = store.put(kind="fact", text="parent record")
    code = f"""
import json, os
from pathlib import Path
from types import SimpleNamespace
from hermes_security import vault
from plugins.memory.lancedb.store import MemoryStore
home = Path(os.environ['HERMES_HOME'])
vault.unlock(home, {PASSWORD!r})
store = MemoryStore(home, SimpleNamespace(ready=False, model_name='test/no-embedding'), 8)
assert store.get({first['id']!r})['text'] == 'parent record'
record = store.put(kind='fact', text='child record')
store.erase([{first['id']!r}])
print(json.dumps({{'id': record['id'], 'pid': os.getpid()}}))
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                            env=dict(os.environ, HERMES_HOME=str(home)),
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["pid"] != os.getpid()
    assert store.get(first["id"]) is None
    for reader in (store, _store(home)):
        record = reader.get(receipt["id"])
        assert record is not None and record["text"] == "child record"
