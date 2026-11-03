"""Held-out lock revocation checks against an otherwise frozen candidate."""
import pytest
from hermes_security import vault
from hermes_security.errors import VaultLockedError

@pytest.mark.parametrize("release", ["lock_now", "clear_vault_cache"])
def test_retained_vault_cannot_write_after_lock(tmp_path,monkeypatch,release):
    monkeypatch.setattr(vault,"_SCRYPT_N",1024)
    home=tmp_path/"synthetic-revocation-owner"
    home.mkdir()
    vault.init_vault(home,"synthetic revocation acceptance password")
    held=vault.unlock(home,"synthetic revocation acceptance password")
    held.write_bytes("config.yaml",b"model: before-lock\n",purpose="config")
    before=(home/"config.yaml").read_bytes()
    try:
        getattr(vault,release)(home) if release=="lock_now" else vault.clear_vault_cache()
        with pytest.raises(VaultLockedError):
            held.write_bytes("config.yaml",b"model: stale-owner\n",purpose="config")
        assert (home/"config.yaml").read_bytes()==before
    finally:
        vault.clear_vault_cache()

@pytest.mark.platforms("posix")
@pytest.mark.parametrize("access", ["retained", "cache"])
def test_live_child_cannot_keep_writing_after_owner_revokes(tmp_path,monkeypatch,access):
    import os,select,subprocess,sys
    from pathlib import Path
    from hermes_security.handoff import prepare_child_key
    from hermes_security.session_authority import start_session_authority

    monkeypatch.setattr(vault,"_SCRYPT_N",1024)
    home=tmp_path/"synthetic-live-child-owner"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME",str(home))
    vault.init_vault(home,"synthetic parent lifetime password")
    held=vault.unlock(home,"synthetic parent lifetime password")
    held.write_bytes("config.yaml",b"model: parent-alive\n",purpose="config")
    before=(home/"config.yaml").read_bytes()
    parent=start_session_authority(home,"held-out-parent")
    descriptor,additions=prepare_child_key(home)
    env=dict(os.environ,**parent.environment(),**additions)
    script="""
import sys
from hermes_constants import get_hermes_home
from hermes_security.handoff import adopt_inherited_key
from hermes_security.vault import get_vault
from hermes_security.errors import VaultLockedError
assert adopt_inherited_key()
held=get_vault(get_hermes_home())
print("ready",flush=True)
assert sys.stdin.read(1)=="x"
try:
    selected=held if sys.argv[1]=="retained" else get_vault(get_hermes_home())
    selected.write_bytes("config.yaml",b"model: revoked-child",purpose="config")
except VaultLockedError:
    print("refused",flush=True)
else:
    print("accepted-stale-write",flush=True)
"""
    process=None
    try:
        process=subprocess.Popen([sys.executable,"-u","-c",script,access],cwd=Path.cwd(),env=env,
                                 pass_fds=(descriptor,),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,text=True)
        assert select.select([process.stdout],[],[],20)[0],"child did not reach lifetime barrier"
        assert process.stdout.readline().strip()=="ready","child failed before revocation"
        parent.close()
        out,error=process.communicate("x",timeout=20)
        assert process.returncode==0,error
        assert out.strip()=="refused",out
        assert (home/"config.yaml").read_bytes()==before
        # The child's loss of authority does not lock its parent.
        assert held.read_bytes("config.yaml",purpose="config")==b"model: parent-alive\n"
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
        os.close(descriptor)
        parent.close()
        vault.clear_vault_cache()
