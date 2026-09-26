"""A long-lived reader must follow another real process's writes, erase and compaction."""
import multiprocessing
from types import SimpleNamespace
from typing import cast

import pytest

pytest.importorskip("lancedb")

from hermes_security import frames, vault
from plugins.memory.lancedb.embedder import Embedder
from plugins.memory.lancedb.store import MemoryStore


def _store(home):
    return MemoryStore(home, cast(Embedder, SimpleNamespace(model_name="offline", ready=False)), dim=8)


def _reader(home, commands, responses):
    vault.unlock(home, "synthetic-refresh-qualification")
    store = _store(home)
    store.refresh()
    responses.put("ready")
    while (mid := commands.get()) is not None:
        record = store.get(mid)
        responses.put(None if record is None else record["text"])


def test_long_lived_process_reader_follows_writer_and_compaction(tmp_path):
    home = tmp_path / "vault"
    home.mkdir()
    vault.init_vault(home, "synthetic-refresh-qualification")
    vault.unlock(home, "synthetic-refresh-qualification")
    ctx = multiprocessing.get_context("spawn")
    commands, responses = ctx.Queue(), ctx.Queue()
    reader = ctx.Process(target=_reader, args=(home, commands, responses))
    reader.start()
    try:
        assert responses.get(timeout=60) == "ready"
        writer = _store(home)
        first = writer.put(kind="fact", text="first")
        commands.put(first["id"])
        assert responses.get(timeout=60) == "first"
        assert writer.erase([first["id"]]) == [first["id"]]
        commands.put(first["id"])
        assert responses.get(timeout=60) is None
        second = writer.put(kind="fact", text="after compaction")
        frames.rewrite(writer.path, lambda rows: rows, purpose="transcript")
        commands.put(second["id"])
        assert responses.get(timeout=60) == "after compaction"
        commands.put(first["id"])
        assert responses.get(timeout=60) is None
        commands.put(None)
        reader.join(timeout=60)
        assert reader.exitcode == 0
    finally:
        if reader.is_alive():
            reader.kill()
            reader.join(timeout=10)
        commands.close()
        responses.close()
