"""Damaged frame streams must not acknowledge hidden writes or erase evidence."""
import os
from pathlib import Path
import subprocess
import sys
import struct

import pytest

from hermes_security import frames, vault
from hermes_security.errors import VaultIntegrityError


@pytest.fixture
def stream(tmp_path):
    home = tmp_path / "home"
    vault.init_vault(home, "synthetic-frame-contract")
    vault.unlock(home, "synthetic-frame-contract")
    path = home / "events.jsonl"
    frames.append(path, b"first", purpose="transcript")
    try:
        yield path
    finally:
        vault.clear_vault_cache()


@pytest.mark.parametrize("operation", ["append", "rewrite"])
def test_interrupted_tail_never_hides_acknowledged_writes(stream, operation):
    first = stream.read_bytes()
    frames.append(stream, b"interrupted", purpose="transcript")
    full = stream.read_bytes()
    for cut in range(len(first) + 1, len(full)):
        damaged = full[:cut]
        stream.write_bytes(damaged)
        try:
            if operation == "append":
                frames.append(stream, b"acknowledged", purpose="transcript")
            else:
                frames.rewrite(stream, lambda rows: rows + [b"acknowledged"], purpose="transcript")
        except VaultIntegrityError:
            assert stream.read_bytes() == damaged
        else:
            # Repair is allowed only if the acknowledged write is readable.
            assert list(frames.read_frames(stream, purpose="transcript")) == [b"first", b"acknowledged"]
            # A rewrite must not discard unclassified bytes implicitly.
            assert operation != "rewrite", "rewrite silently discarded an incomplete suffix"


@pytest.mark.parametrize("damage", ["tag", "length", "envelope"])
def test_corruption_is_not_successful_empty_read_or_rewrite(stream, damage):
    raw = stream.read_bytes()
    damaged = {"tag": raw[:-1] + bytes([raw[-1] ^ 1]),
               "length": struct.pack(">I", 0),
               "envelope": b"HRMVAULT\x00" + raw}[damage]
    stream.write_bytes(damaged)
    for action in (lambda: list(frames.read_frames(stream, purpose="transcript")),
                   lambda: frames.append(stream, b"new", purpose="transcript"),
                   lambda: frames.rewrite(stream, lambda rows: rows, purpose="transcript")):
        with pytest.raises(VaultIntegrityError):
            action()
        assert stream.read_bytes() == damaged


def test_concurrent_process_append_and_rewrite_preserve_records(stream):
    code = """
import sys
from pathlib import Path
from hermes_security import frames, vault
path = Path(sys.argv[1])
vault.unlock(path.parent, 'synthetic-frame-contract')
sys.stdin.readline()
for index in range(20):
    frames.append(path, f'{sys.argv[2]}:{index}'.encode(), purpose='transcript')
    if index % 3 == 0:
        frames.rewrite(path, lambda rows: rows, purpose='transcript')
"""
    children = [subprocess.Popen([sys.executable, "-c", code, str(stream), str(i)],
                                 cwd=Path(__file__).resolve().parents[2],
                                 env=dict(os.environ, HERMES_HOME=str(stream.parent)),
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True) for i in range(3)]
    try:
        for child in children:
            assert child.stdin is not None
            child.stdin.write("go\n")
            child.stdin.flush()
        for child in children:
            out, err = child.communicate(timeout=60)
            assert child.returncode == 0, out + err
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
    expected = [b"first"] + [f"{i}:{j}".encode() for i in range(3) for j in range(20)]
    assert sorted(frames.read_frames(stream, purpose="transcript")) == sorted(expected)
