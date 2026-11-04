"""A human re-unlock creates a new DB generation; retained handles never adopt it."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys

import pytest

from hermes_security import vault
from hermes_security.errors import VaultLockedError
from hermes_state import SessionDB, registry


_BORROWER = '''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hermes_security.handoff import adopt_inherited_key
from hermes_security import vault
from hermes_security.errors import VaultLockedError
from hermes_security.session_authority import capture_inherited_authority, record_inherited_authority
from hermes_state import SessionDB
assert adopt_inherited_key()
home = Path(sys.argv[2])
owner = vault.get_vault(home)
authority = capture_inherited_authority(home)
envelope = owner.encrypt(b'synthetic custody envelope', purpose='probe', relpath='probe', authority=authority)
try:
    owner.decrypt(envelope, purpose='probe', relpath='probe', authority=None)
except VaultLockedError:
    pass
else:
    raise AssertionError('explicit missing authority admitted a borrowed vault')
db = SessionDB(home / 'state.db')
assert db.get_session('preserved') is not None
print('ready', flush=True)
record_inherited_authority(home, json.loads(sys.stdin.readline()))
print('retargeted', flush=True)
sys.stdin.readline()
seen = {}
for name, operation in [('readonly', lambda: db._connect_read_only(1)), ('writer', db._open_writer_conn)]:
    try:
        connection = operation()
        seen[name] = connection.execute("SELECT count(*) FROM sessions WHERE id='preserved'").fetchone()[0] == 1
        connection.close()
    except Exception as exc:
        seen[name] = type(exc).__name__
try:
    plaintext = owner.decrypt(envelope, purpose='probe', relpath='probe', authority=authority)
    fresh_envelope = owner.encrypt(plaintext, purpose='probe', relpath='probe', authority=authority)
    seen['envelope'] = owner.decrypt(fresh_envelope, purpose='probe', relpath='probe', authority=authority) == plaintext
except Exception as exc:
    seen['envelope'] = type(exc).__name__
db.close()
try:
    seen['reopened'] = db.get_session('preserved') is not None
except Exception as exc:
    seen['reopened'] = type(exc).__name__
print(json.dumps(seen), flush=True)
db.close()
'''


def _line(child):
    assert select.select([child.stdout], [], [], 30)[0], "native borrower missed protocol boundary"
    result = child.stdout.readline()
    assert result, "native borrower exited before protocol boundary"
    return result.strip()


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("close_original", [False, True], ids=["original-live", "original-revoked"])
def test_future_connections_and_reopen_keep_original_borrowed_lineage(tmp_path, monkeypatch, close_original):
    from hermes_security.handoff import KEY_FD_ENV, prepare_child_key
    from hermes_security.session_authority import ENV_NAME, start_session_authority

    home = tmp_path / "owner"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(home, "synthetic native database custody credential")
    vault.unlock(home, "synthetic native database custody credential")
    with SessionDB(home / "state.db") as db:
        db.create_session("preserved", "cli")
    original = start_session_authority(home, "original-lineage")
    replacement = start_session_authority(home, "replacement-lineage")
    descriptor, addition = prepare_child_key(home, retain_session=True, source_grant=original.environment()[ENV_NAME])
    env = {key: value for key, value in os.environ.items() if key not in {KEY_FD_ENV, ENV_NAME}}
    env.update(HERMES_HOME=str(home), HERMES_SESSION_ID=original.session_id, **original.environment(), **addition)
    child = None
    try:
        try:
            child = subprocess.Popen(
                [sys.executable, "-I", "-c", _BORROWER, str(Path(__file__).resolve().parents[2]), str(home)],
                env=env, pass_fds=(descriptor,), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True,
            )
        finally:
            os.close(descriptor)
        assert _line(child) == "ready"
        child.stdin.write(json.dumps(replacement.environment()[ENV_NAME]) + "\n")
        child.stdin.flush()
        assert _line(child) == "retargeted"
        (original if close_original else replacement).close()
        child.stdin.write("continue\n")
        child.stdin.flush()
        observed = json.loads(_line(child))
        child.wait(timeout=30)
        assert child.returncode == 0
        expected = "VaultLockedError" if close_original else True
        assert observed == {name: expected for name in ("readonly", "writer", "reopened", "envelope")}
        assert (replacement if close_original else original).is_live()
        with SessionDB(home / "state.db") as db:
            assert db.get_session("preserved") is not None
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        original.close()
        replacement.close()
        vault.clear_vault_cache()


def test_reunlock_retires_registry_generation_and_fences_future_opens(tmp_path, monkeypatch):
    home = tmp_path / "owner"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    password = "synthetic database epoch credential"
    vault.init_vault(home, password)
    vault.unlock(home, password)
    path = home / "state.db"
    old = registry.acquire(path)
    old.create_session("preserved", "cli")
    old.append_message("preserved", "user", "synthetic preserved history")
    reader = SessionDB(path, read_only=True)
    retained_read = old._connect_read_only(timeout=1)
    fresh = None
    try:
        assert len(reader.get_messages("preserved")) == 1
        vault.lock_now(home)
        vault.unlock(home, password)
        fresh = registry.acquire(path)
        assert fresh is not old
        assert len(fresh.get_messages("preserved")) == 1
        fresh.create_session("new-owner", "cli")
        for operation in (lambda: old.get_messages("preserved"), lambda: reader.get_messages("preserved"),
                          lambda: retained_read.execute("SELECT 1"), lambda: old._connect_read_only(timeout=1),
                          old._open_writer_conn):
            with pytest.raises(VaultLockedError):
                operation()
        # The legacy closed-handle healing path must not acquire the new human's custody either.
        old._conn.close()
        old._conn = None
        with pytest.raises(VaultLockedError):
            old.get_messages("preserved")
        assert fresh.get_session("new-owner") is not None
    finally:
        retained_read.close()
        reader.close()
        registry.release(old)
        if fresh is not None:
            registry.release(fresh)
        vault.clear_vault_cache()
