"""Native dispatch and trusted handoffs preserve exact owning lifetimes."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import threading

import pytest

from hermes_security import vault
from tests.waiting import wait_until

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.platforms("linux", "macos")


@pytest.fixture
def home(tmp_path, monkeypatch):
    selected = tmp_path / 'owner'
    selected.mkdir(mode=0o700)
    monkeypatch.setenv('HERMES_HOME', str(selected))
    monkeypatch.setenv('HERMES_RUNTIME_DIR', str(tmp_path / 'runtime'))
    monkeypatch.setattr(vault, '_SCRYPT_N', 1024)
    vault.init_vault(selected, 'synthetic lifetime credential')
    vault.unlock(selected, 'synthetic lifetime credential')
    yield selected
    vault.clear_vault_cache()


@pytest.mark.parametrize("pause", ["before-admission", "before-handoff"])
def test_native_start_cannot_outlive_its_owner_or_borrow_sibling(home, tmp_path, monkeypatch, pause):
    from hermes_security import authority_native as native
    from hermes_security.session_authority import ENV_NAME, start_session_authority
    from hermes_cli import _launchers
    owner = start_session_authority(home, 'closing-owner')
    sibling = start_session_authority(home, 'surviving-sibling')
    report = tmp_path / 'spawn-observation.json'
    child = '''
import json,sys
from pathlib import Path
from hermes_security.handoff import adopt_inherited_key
from hermes_security.session_authority import inherited_grant
adopted=adopt_inherited_key()
raw=inherited_grant(Path(sys.argv[1]))
Path(sys.argv[2]).write_text(json.dumps({'child_started':True,'adopted':adopted,'used_sibling_grant':bool(raw and json.loads(raw)['session_id']=='surviving-sibling')}))
'''
    monkeypatch.setattr(_launchers, 'runtime_command', lambda *args: [sys.executable, '-c', child, str(home), str(report)])
    entered, resume = threading.Event(), threading.Event()
    from tools.environments import local
    target, name = (native, "checked_arguments") if pause == "before-admission" else (local, "served_profile_child_env")
    original = getattr(target, name)
    def paused(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert resume.wait(20)
        return result
    monkeypatch.setattr(target, name, paused)
    request = {**json.loads(owner.environment()[ENV_NAME]), 'op':'native.start','arguments':['config','get','agent.max_turns']}
    outcome = []
    thread = threading.Thread(target=lambda: outcome.append(owner.respond(request)))
    closing = threading.Thread(target=owner.close)
    thread.start()
    try:
        assert entered.wait(20)
        closing.start()
        assert wait_until(lambda: not owner.is_live(), timeout=20)
        resume.set()
        thread.join(20)
        closing.join(20)
        assert not closing.is_alive()
        assert not thread.is_alive()
        if outcome[0].get('ok'):
            native._jobs[outcome[0]['job']][1].finished.wait(20)
        state = json.loads(report.read_text()) if report.exists() else {'child_started':False,'used_sibling_grant':False}
        assert not state['child_started'] and not state['used_sibling_grant'], state
        assert outcome[0].get('ok') is not True
        monkeypatch.setattr(target, name, original)
        live_request = {**json.loads(sibling.environment()[ENV_NAME]),
                        'op': 'native.start', 'arguments': ['config', 'get', 'agent.max_turns']}
        live_response = sibling.respond(live_request)
        assert live_response.get('ok') is True
        assert native._jobs[live_response['job']][1].finished.wait(20)
        assert json.loads(report.read_text()) == {
            'child_started': True, 'adopted': True, 'used_sibling_grant': True}
    finally:
        resume.set()
        thread.join(20)
        if closing.ident is not None:
            closing.join(20)
        native.close_native(owner)
        owner.close()
        sibling.close()

_GRAND = '''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from hermes_security import vault
from hermes_security.errors import VaultError
from hermes_security.handoff import adopt_inherited_key
home=Path(sys.argv[2])
assert adopt_inherited_key()
print('ready',flush=True)
sys.stdin.readline()
try:
    vault.get_vault(home,allow_env_unlock=False).write_bytes('detached-output',b'synthetic',purpose='probe')
    usable=True
except VaultError:
    usable=False
print(json.dumps({'usable':usable}),flush=True)
'''
_BORROW = '''
import json,os,subprocess,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from hermes_security.handoff import adopt_inherited_key,prepare_child_key
from hermes_security.errors import VaultError
assert adopt_inherited_key()
try:
    fd,extra=prepare_child_key(Path(sys.argv[2]),retain_session=False)
except VaultError:
    print('ready',flush=True)
    sys.stdin.readline()
    print(json.dumps({'usable':False}),flush=True)
    raise SystemExit(0)
env={k:v for k,v in os.environ.items() if k not in {'HERMES_SESSION_VAULT','HERMES_VAULT_KEY_FD','HERMES_VAULT_PRIVATE_KEY','HERMES_MASTER_PASSWORD'}}
env.update(extra)
try:
    child=subprocess.Popen([sys.executable,'-I','-c',sys.argv[3],sys.argv[1],sys.argv[2]],env=env,pass_fds=(fd,),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
finally:
    os.close(fd)
assert child.stdout.readline().strip()=='ready'
print('ready',flush=True)
sys.stdin.readline()
stdout,stderr=child.communicate('closed\\n',timeout=30)
assert child.returncode==0
print(stdout.strip(),flush=True)
'''


@pytest.mark.parametrize("borrowed", [True, False], ids=["borrowed-retains-lineage", "owner-transfers-custody"])
def test_detached_handoff_preserves_borrowed_lifetime_and_owner_transfer(home, borrowed):
    from agent.session_context import scoped_current_session_id
    from hermes_security.handoff import prepare_child_key
    from hermes_security.session_authority import start_session_authority
    authority=start_session_authority(home,'upstream-owner')
    with scoped_current_session_id(authority.session_id):
        fd,extra=prepare_child_key(home, retain_session=borrowed)
    env={k:v for k,v in os.environ.items() if k not in {'HERMES_SESSION_VAULT','HERMES_VAULT_KEY_FD','HERMES_VAULT_PRIVATE_KEY','HERMES_MASTER_PASSWORD'}}
    env.update(extra)
    try:
        command = [sys.executable, '-I', '-c', _BORROW if borrowed else _GRAND, str(ROOT), str(home)]
        if borrowed:
            command.append(_GRAND)
        child=subprocess.Popen(command,env=env,pass_fds=(fd,),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    finally:
        os.close(fd)
    try:
        assert select.select([child.stdout],[],[],30)[0]
        assert child.stdout.readline().strip()=='ready'
        authority.close()
        stdout,_stderr=child.communicate('closed\n',timeout=30)
        assert child.returncode==0, 'synthetic borrower failed'
        state=json.loads(stdout)
        assert state['usable'] is not borrowed, state
    finally:
        authority.close()
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)
