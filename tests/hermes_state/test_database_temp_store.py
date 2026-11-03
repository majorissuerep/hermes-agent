"""Encrypted state keeps memory temporaries despite legacy performance configuration."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hermes_security import vault
from hermes_state import SessionDB

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "synthetic database temporary storage password"


@pytest.fixture
def encrypted_home(tmp_path, monkeypatch):
    home = tmp_path / "owner"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(home, PASSWORD)
    owner = vault.unlock(home, PASSWORD)
    yield home, owner
    vault.clear_vault_cache()


@pytest.mark.parametrize("configured", [0, 1, 2])
def test_legacy_temp_store_cannot_override_encrypted_connections(encrypted_home, configured, caplog):
    home, owner = encrypted_home
    configuration = f"database:\n  temp_store: {configured}\n".encode()
    owner.write_bytes("config.yaml", configuration, purpose="config")
    writer = SessionDB(home / "state.db")
    try:
        assert writer._conn.execute("PRAGMA temp_store").fetchone()[0] == 2
        reader = SessionDB(home / "state.db", read_only=True)
        try:
            assert reader._conn.execute("PRAGMA temp_store").fetchone()[0] == 2
        finally:
            reader.close()
    finally:
        writer.close()
    assert owner.read_bytes("config.yaml", purpose="config") == configuration
    if configured != 2:
        assert "effective temporary storage is MEMORY (2)" in caplog.text
        assert "saved configuration has not been changed" in caplog.text


_SPILL = '''
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hermes_security import vault
from hermes_state import SessionDB
home, spill, mode = Path(sys.argv[2]), sys.argv[3], sys.argv[4]
vault.unlock(home, sys.stdin.read())
db = SessionDB(home / 'state.db')
conn = db._conn
if mode == 'file-control':
    conn.execute('PRAGMA temp_store = FILE')
conn.execute('PRAGMA cache_size = 1')
conn.execute('PRAGMA temp.cache_size = 1')
conn.execute('CREATE TEMP TABLE scratch(v TEXT)')
sentinel = 'SYNTHETIC_SQLITE_TEMP_STORAGE_SENTINEL_7fcd3b81'
conn.executemany('INSERT INTO scratch VALUES (?)',
                 ((sentinel + str(i) + os.urandom(64).hex(),) for i in range(20000)))
rows = conn.execute('SELECT v FROM scratch ORDER BY v')
assert rows.fetchone() is not None
files, plaintext = 0, False
for fd in os.listdir('/proc/self/fd'):
    try:
        target = os.readlink('/proc/self/fd/' + fd)
        if target.startswith(spill + os.sep):
            files += 1
            with open('/proc/self/fd/' + fd, 'rb') as stream:
                plaintext = plaintext or sentinel.encode() in stream.read()
    except OSError:
        continue
print(json.dumps({'mode': conn.execute('PRAGMA temp_store').fetchone()[0],
                  'files': files, 'plaintext': plaintext}))
db.close()
'''


def _spill_probe(home, spill, mode):
    env = {name: value for name, value in os.environ.items()
           if name not in {"HERMES_SESSION_VAULT", "HERMES_VAULT_KEY_FD", "HERMES_VAULT_PRIVATE_KEY"}}
    env.update(HERMES_HOME=str(home), SQLITE_TMPDIR=str(spill), TMPDIR=str(spill))
    result = subprocess.run([sys.executable, "-I", "-c", _SPILL, str(ROOT), str(home), str(spill), mode],
                            env=env, input=PASSWORD, text=True, capture_output=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.platforms("linux")
def test_encrypted_session_database_opens_no_plaintext_spill_files(encrypted_home, tmp_path):
    home, owner = encrypted_home
    owner.write_bytes("config.yaml", b"database:\n  temp_store: 1\n", purpose="config")
    spill = tmp_path / "spill"
    spill.mkdir(mode=0o700)

    control = _spill_probe(home, spill, "file-control")
    assert control["mode"] == 1 and control["files"] > 0 and control["plaintext"], "spill probe is blind"
    assert _spill_probe(home, spill, "hermes") == {"mode": 2, "files": 0, "plaintext": False}
