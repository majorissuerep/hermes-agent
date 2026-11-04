"""Previously obtained vault objects obey the lifetime of their lending session."""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import subprocess
import sys

import pytest

from hermes_security import vault

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "synthetic cached vault authority password"
pytestmark = pytest.mark.platforms("linux", "macos")


@pytest.fixture
def home(tmp_path, monkeypatch):
    selected = tmp_path / "owner"
    selected.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(selected))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(selected, PASSWORD)
    owner = vault.unlock(selected, PASSWORD)
    owner.write_bytes("payload", b"synthetic retained payload", purpose="probe")
    yield selected, owner
    vault.clear_vault_cache()


_CACHED = '''
import json, sys
sys.path.insert(0, sys.argv[1])
from hermes_security import vault
from hermes_security.errors import VaultLockedError
from hermes_security.handoff import adopt_inherited_key
home, access = sys.argv[2:]
assert adopt_inherited_key()
held = vault.get_vault(home, allow_env_unlock=False)
exported = held.master_key
print(json.dumps({'ready': True}), flush=True)
sys.stdin.readline()
calls = {'snapshot': lambda: held.master_key,
         'derive': lambda: held.derive_key('probe'),
         'encrypt': lambda: held.encrypt(b'synthetic', purpose='probe', relpath='payload'),
         'read': lambda: held.read_bytes('payload', purpose='probe'),
         'write': lambda: held.write_bytes('after-close', b'synthetic', purpose='probe')}
try:
    calls[access]()  # Deliberately no registry lookup between session close and key use.
    refused = False
except VaultLockedError:
    refused = True
print(json.dumps({'refused': refused, 'wiped': held.protection_status()['wiped'],
                  'exported_copy_survives': len(exported) == 32 and any(exported)}))
'''


_REUNLOCK_RACE = '''
import json, sys, threading
sys.path.insert(0, sys.argv[1])
from hermes_security import vault
from hermes_security.errors import VaultLockedError
from hermes_security.handoff import adopt_inherited_key
home = sys.argv[2]
assert adopt_inherited_key()
held = vault.get_vault(home, allow_env_unlock=False)
checked, resume = threading.Event(), threading.Event()
original = vault._require_live_authority
def paused_failure(selected):
    try:
        original(selected)  # Real upstream check; pause only after its refusal.
    except VaultLockedError:
        checked.set()
        assert resume.wait(20), 'owner unlock did not resume the revoker'
        raise
vault._require_live_authority = paused_failure
failures = []
def old_reader():
    try:
        held.master_key
    except VaultLockedError:
        failures.append(True)
print(json.dumps({'ready': True}), flush=True)
password = sys.stdin.readline().strip()
thread = threading.Thread(target=old_reader)
thread.start()
try:
    assert checked.wait(20), 'stale key access did not check its lender'
    owner = vault.unlock(home, password)
finally:
    resume.set()
    thread.join(20)
    vault._require_live_authority = original
assert not thread.is_alive()
owner.write_bytes('owner-after-race', b'synthetic owner data', purpose='probe')
print(json.dumps({'old_refused': failures == [True], 'old_wiped': held.protection_status()['wiped'],
                  'new_is_distinct': owner is not held,
                  'new_held': owner.protection_status()['retained_key_locked'],
                  'new_readable': owner.read_bytes('owner-after-race', purpose='probe') == b'synthetic owner data'}))
'''


def _start(home, authority, script, *arguments):
    from agent.session_context import scoped_current_session_id
    from hermes_security.handoff import prepare_child_key

    with scoped_current_session_id(authority.session_id):
        fd, additions = prepare_child_key(home)
    env = {name: value for name, value in os.environ.items()
           if name not in {"HERMES_SESSION_VAULT", "HERMES_VAULT_KEY_FD", "HERMES_VAULT_PRIVATE_KEY"}}
    env.update(additions, HERMES_HOME=str(home))
    try:
        process = subprocess.Popen([sys.executable, "-I", "-c", script, str(ROOT), str(home), *arguments],
                                   env=env, pass_fds=(fd,), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
    finally:
        os.close(fd)
    try:
        assert select.select([process.stdout], [], [], 30)[0], "borrowed child did not start"
        assert json.loads(process.stdout.readline()) == {"ready": True}
    except BaseException:
        _stop(process)
        raise
    return process


def _finish(process, message):
    stdout, stderr = process.communicate(message + "\n", timeout=60)
    assert process.returncode == 0, stderr
    return json.loads(stdout)


def _stop(process):
    if process.poll() is None:
        process.kill()
        process.communicate(timeout=10)


def test_cached_vault_key_access_refuses_closed_lender_and_preserves_sibling(home):
    from hermes_security.session_authority import start_session_authority

    selected, owner = home
    lender = start_session_authority(selected, "cached-lender")
    sibling = start_session_authority(selected, "independent-lender")
    children = []
    try:
        for access in ("snapshot", "derive", "encrypt", "read", "write"):
            children.append(_start(selected, lender, _CACHED, access))
        independent = _start(selected, sibling, _CACHED, "read")
        children.append(independent)
        lender.close()
        for process in children[:-1]:
            assert _finish(process, "closed") == {"refused": True, "wiped": True, "exported_copy_survives": True}
        assert _finish(independent, "still-live") == {"refused": False, "wiped": False, "exported_copy_survives": True}
        assert owner.read_bytes("payload", purpose="probe") == b"synthetic retained payload"
    finally:
        for process in children:
            _stop(process)
        lender.close()
        sibling.close()


def test_old_revocation_cannot_wipe_a_concurrent_human_reunlock(home):
    from hermes_security.session_authority import start_session_authority

    selected, owner = home
    authority = start_session_authority(selected, "racing-lender")
    process = _start(selected, authority, _REUNLOCK_RACE)
    try:
        authority.close()
        assert _finish(process, PASSWORD) == {"old_refused": True, "old_wiped": True,
                                             "new_is_distinct": True, "new_held": True, "new_readable": True}
        assert owner.read_bytes("payload", purpose="probe") == b"synthetic retained payload"
    finally:
        _stop(process)
        authority.close()
