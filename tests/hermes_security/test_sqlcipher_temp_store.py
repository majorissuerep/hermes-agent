"""Hermes SQLCipher connections keep SQLite temporaries in memory, never in temp files.

Temp tables, sorter spill, temp indices and VACUUM's scratch database follow
``temp_store``. Memory temporaries are not swap-proof: on a host with active
swap the kernel may still page them out. These tests prove only that SQLite
creates no temporary file.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hermes_security import vault

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "synthetic temp-store password"
MEMORY = 2

# Opens a vaulted scratch DB through hermes_security.sqlite.connect, forces a
# temp table and an ORDER BY to outgrow a one-page cache, and lists this
# process's descriptors under the spill directory while both are still live.
_CHILD = '''
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hermes_security import sqlite, vault
home, spill, mode = Path(sys.argv[2]), sys.argv[3], sys.argv[4]
vault.unlock(home, sys.stdin.read())
conn = sqlite.connect(home / "scratch.db")
if mode == "file":
    conn.execute("PRAGMA temp_store = FILE")
conn.execute("PRAGMA cache_size = 1")
conn.execute("PRAGMA temp.cache_size = 1")
conn.execute("CREATE TEMP TABLE scratch(v TEXT)")
conn.executemany("INSERT INTO scratch VALUES (?)", ((os.urandom(64).hex(),) for _ in range(20000)))
rows = conn.execute("SELECT v FROM scratch ORDER BY v")
assert rows.fetchone() is not None
found = []
for fd in os.listdir("/proc/self/fd"):
    try:
        target = os.readlink(f"/proc/self/fd/{fd}")
    except OSError:
        continue
    if target.startswith(spill + os.sep):
        found.append(target)
temp_store = conn.execute("PRAGMA temp_store").fetchone()[0]
print(json.dumps({"temp_store": temp_store, "files": sorted(found)}))
'''


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    target = tmp_path / "owner"
    target.mkdir()
    vault.init_vault(target, PASSWORD)
    vault.unlock(target, PASSWORD)
    yield target
    vault.clear_vault_cache()


def _spill_probe(home, spill, mode):
    env = {name: value for name, value in os.environ.items()
           if name not in {"HERMES_SESSION_VAULT", "HERMES_VAULT_KEY_FD", "HERMES_VAULT_PRIVATE_KEY"}}
    env.update(HERMES_HOME=str(home), SQLITE_TMPDIR=str(spill), TMPDIR=str(spill))
    result = subprocess.run([sys.executable, "-I", "-c", _CHILD, str(ROOT), str(home), str(spill), mode],
                            input=PASSWORD, env=env, text=True, capture_output=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_connect_selects_memory_temporaries_without_caller_configuration(home):
    from hermes_security import sqlite

    conn = sqlite.connect(home / "state.db")
    try:
        assert conn.execute("PRAGMA temp_store").fetchone()[0] == MEMORY
    finally:
        conn.close()


@pytest.mark.platforms("linux")
def test_spilling_temporaries_open_no_temp_file_while_file_mode_does(home, tmp_path):
    spill = tmp_path / "spill"
    spill.mkdir(mode=0o700)

    hermes = _spill_probe(home, spill, "hermes")
    control = _spill_probe(home, spill, "file")

    # The control arm proves the probe sees real temp files: the identical
    # workload under temp_store=FILE holds unlinked etilqs_* files in the
    # spill directory while the temp table and the sort are live.
    assert control["temp_store"] == 1
    assert control["files"], "FILE control produced no temp file; the probe is blind"
    assert all(path.endswith(" (deleted)") for path in control["files"])
    assert hermes == {"temp_store": MEMORY, "files": []}
