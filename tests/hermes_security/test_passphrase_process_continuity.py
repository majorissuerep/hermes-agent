"""Real-process rotation acceptance; execute in disposable native qualification pods only."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time

import pytest

from hermes_security import vault
from hermes_security.passphrase import change_passphrase

pytestmark = pytest.mark.platforms("linux", "macos")
ROOT = Path(__file__).resolve().parents[2]


def _child(home, password=None, grant=None, handoff=None, key=None):
    code = '''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from hermes_security import vault, sqlite
from hermes_security.handoff import adopt_inherited_key
request=json.load(sys.stdin)
home=Path(request['home'])
try:
 if request.get('password') is not None: vault.unlock(home,request['password'])
 elif request.get('key') is not None: vault.unlock_with_private_key(home,bytes.fromhex(request['key']))
 else: assert adopt_inherited_key()
 conn=sqlite.connect(f'file:{home / "state.db"}?mode=ro',uri=True)
 rows=conn.execute('SELECT content FROM history').fetchall();conn.close()
 print(json.dumps({'rows':rows,'unlocked':True}))
except Exception as exc:
 print(json.dumps({'error':type(exc).__name__}));sys.exit(2)
'''
    env = {name: value for name, value in os.environ.items()
           if name not in {"HERMES_SESSION_VAULT", "HERMES_VAULT_KEY_FD", "HERMES_VAULT_PRIVATE_KEY"}}
    env["HERMES_HOME"] = str(home)
    env.update(grant or {})
    if handoff:
        env.update(handoff[1])
    result = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)],
                            input=json.dumps({"home": str(home), "password": password,
                                              "key": key.hex() if key else None}),
                            env=env, pass_fds=(handoff[0],) if handoff else (),
                            text=True, capture_output=True, timeout=30, check=False)
    return result, json.loads(result.stdout)


def test_fresh_passwords_key_slot_prepared_fd_and_live_session_grant_after_rotation(tmp_path):
    from hermes_security import sqlite
    from hermes_security.handoff import prepare_child_key
    from hermes_security.session_authority import start_session_authority

    home = tmp_path / "owner"
    home.mkdir()
    vault.init_vault(home, "original synthetic secret")
    vault.unlock(home, "original synthetic secret")
    private, public = vault.generate_keypair()
    vault.add_key_slot(home, public_key_raw=public, unlocked_vault=vault.get_vault(home))
    connection = sqlite.connect(home / "state.db")
    connection.execute("CREATE TABLE history(content TEXT)")
    connection.execute("INSERT INTO history VALUES ('retained random transcript')")
    connection.commit()
    connection.close()
    before = (home / "state.db").read_bytes()
    authority = start_session_authority(home, "rotation-owner")
    handoff = prepare_child_key()
    try:
        grant = authority.environment()
        change_passphrase(home, "replacement synthetic secret")
        result, row = _child(home, "original synthetic secret")
        assert result.returncode == 2 and row == {"error": "WrongMasterPasswordError"}
        for kwargs in ({"password": "replacement synthetic secret"}, {"key": private},
                       {"handoff": handoff}, {"grant": grant}):
            result, row = _child(home, **kwargs)
            assert result.returncode == 0, result.stderr
            assert row == {"rows": [["retained random transcript"]], "unlocked": True}
        assert (home / "state.db").read_bytes() == before
        authority.close()
        result, row = _child(home, grant=grant)
        assert result.returncode == 2 and row.get("error")
    finally:
        authority.close()
        os.close(handoff[0])
        vault.lock_now(home)


def _metadata_worker(home, action, public, start, ready):
    from hermes_security.handoff import prepare_child_key

    code = '''
import json,os,sys
sys.path.insert(0,sys.argv[1])
from hermes_security import vault
from hermes_security.handoff import adopt_inherited_key
from hermes_security.passphrase import change_passphrase
request=json.load(sys.stdin)
assert adopt_inherited_key()
os.write(request['ready'],b'1')
assert os.read(request['start'],1)==b'1'
if request['action']=='rotate': change_passphrase(request['home'],request['password'])
else: vault.add_key_slot(request['home'],public_key_raw=bytes.fromhex(request['public']),
                         unlocked_vault=vault.get_vault(request['home'],allow_env_unlock=False))
print('committed')
'''
    handoff = prepare_child_key()
    env = {name: value for name, value in os.environ.items()
           if name not in {"HERMES_SESSION_VAULT", "HERMES_VAULT_KEY_FD", "HERMES_VAULT_PRIVATE_KEY"}}
    env.update(handoff[1], HERMES_HOME=str(home))
    try:
        process = subprocess.Popen([sys.executable, "-I", "-c", code, str(ROOT)], env=env,
                                   pass_fds=(handoff[0], start, ready), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    finally:
        os.close(handoff[0])
    try:
        process.stdin.write(json.dumps({"home": str(home), "action": action, "public": public.hex(),
                                       "password": "concurrent replacement", "start": start, "ready": ready}))
        process.stdin.close()
        process.stdin = None
    except BaseException:
        process.kill()
        process.communicate(timeout=10)
        raise
    return process


def test_independent_metadata_writers_preserve_rotation_and_new_key_slot(tmp_path):
    home = tmp_path / "owner"
    home.mkdir()
    vault.init_vault(home, "old concurrent password")
    vault.unlock(home, "old concurrent password")
    # The same real encrypted DB oracle is used by each fresh verifier below.
    from hermes_security import sqlite
    connection = sqlite.connect(home / "state.db")
    connection.execute("CREATE TABLE history(content TEXT)")
    connection.execute("INSERT INTO history VALUES ('concurrent original history')")
    connection.commit()
    connection.close()
    private, public = vault.generate_keypair()
    start, release = os.pipe()
    received, ready = os.pipe()
    children = []
    try:
        for action in ("rotate", "slot"):
            children.append(_metadata_worker(home, action, public, start, ready))
        deadline, count = time.monotonic() + 20, 0
        while count != 2:
            readable, _, _ = select.select([received], [], [], max(0, deadline - time.monotonic()))
            assert readable, "metadata writers did not reach the common start boundary"
            count += len(os.read(received, 2 - count))
        os.write(release, b"11")
        for child in children:
            stdout, stderr = child.communicate(timeout=30)
            assert child.returncode == 0 and stdout.strip() == "committed", stderr
        for kwargs in ({"password": "concurrent replacement"}, {"key": private}):
            result, row = _child(home, **kwargs)
            assert result.returncode == 0, result.stderr
            assert row["rows"] == [["concurrent original history"]]
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)
        for fd in (start, release, received, ready):
            os.close(fd)
        vault.lock_now(home)
