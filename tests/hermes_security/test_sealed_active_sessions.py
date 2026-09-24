"""Fork regression: the active-session registry is sealed state in a vaulted home.

Incident (luoman-MS73-HB1, 2026-09-25): migration sealed ``runtime/active_sessions.json``;
``_read_entries`` opened it with a plain ``open()`` and hit ciphertext, so every interactive
``hermes chat`` died before the first turn with "could not read the active-session registry".
Writes went through the plaintext ``atomic_json_write``, which would clobber the envelope.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pytest

from hermes_security import migrate as mig
from hermes_security import vault as hv

PW = "sealed-registry-pw"


@pytest.fixture()
def sealed_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    (home / "runtime").mkdir(parents=True)
    (home / "runtime" / "active_sessions.json").write_text(
        '{"entries": [{"lease_id": "L1", "session_id": "s1", "pid": 999999, "surface": "cli"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert mig.migrate_home(home, PW).ok
    hv.clear_vault_cache()
    hv.unlock(home, PW)
    yield home
    hv.clear_vault_cache()


def test_read_entries_reads_the_sealed_registry(sealed_home):
    from hermes_cli import active_sessions as reg

    path = sealed_home / "runtime" / "active_sessions.json"
    assert path.read_bytes().startswith(b"HRMVAULT\x00")  # fixture must seal it
    entries = reg._read_entries(path)
    assert [e["lease_id"] for e in entries] == ["L1"]


def test_write_entries_roundtrips_through_the_envelope(sealed_home):
    from hermes_cli import active_sessions as reg

    path = sealed_home / "runtime" / "active_sessions.json"
    reg._write_entries(path, [{"lease_id": "L2", "session_id": "s2", "pid": 424242, "surface": "gateway"}])
    assert path.read_bytes().startswith(b"HRMVAULT\x00")  # still sealed after write
    entries = reg._read_entries(path)
    assert [e["lease_id"] for e in entries] == ["L2"]


def test_missing_registry_is_empty(sealed_home):
    from hermes_cli import active_sessions as reg

    assert reg._read_entries(sealed_home / "runtime" / "absent.json") == []
