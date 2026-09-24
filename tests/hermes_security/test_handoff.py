"""Vault handoff to a detached child (``hermes_security/handoff.py``): the child ends up able to read what the
parent sealed, the key crosses only an inherited pipe (never env/argv), and a forged key is refused."""

import os
import subprocess
import sys

import pytest

from hermes_security import vault as vault_mod
from hermes_security.errors import WrongMasterPasswordError

_CHILD = """
import os, sys
from hermes_security.handoff import KEY_FD_ENV, adopt_inherited_key
from hermes_security import vault as vault_mod
assert adopt_inherited_key() is True
assert KEY_FD_ENV not in os.environ  # consumed exactly once
v = vault_mod.get_vault(sys.argv[1], allow_env_unlock=False)
sys.stdout.write(v.read_bytes(os.path.join(sys.argv[1], "secret.bin"), purpose="test").decode())
"""


@pytest.mark.linux_only  # pass_fds inheritance; the Windows host launcher does not hand off (documented)
def test_child_reads_parent_sealed_state_via_inherited_fd(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    vault_mod.init_vault(home, "correct horse")
    parent_vault = vault_mod.unlock(home, "correct horse")
    parent_vault.write_bytes(home / "secret.bin", b"sealed by the parent", purpose="test")
    monkeypatch.delenv("HERMES_MASTER_PASSWORD", raising=False)

    from hermes_security.handoff import prepare_child_key

    read_fd, extra_env = prepare_child_key()
    try:
        env = {**os.environ, **extra_env}
        assert "correct horse" not in "".join(env.values())
        out = subprocess.run([sys.executable, "-c", _CHILD, str(home)], env=env, pass_fds=(read_fd,),
                             capture_output=True, text=True, timeout=60)
    finally:
        os.close(read_fd)
        vault_mod.clear_vault_cache()
    assert out.returncode == 0, out.stderr
    assert out.stdout == "sealed by the parent"


def test_forged_key_is_refused(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    vault_mod.init_vault(home, "correct horse")
    vault_mod.clear_vault_cache()
    with pytest.raises(WrongMasterPasswordError):
        vault_mod.adopt_unlocked_key(home, os.urandom(32))
    assert not vault_mod.is_unlocked(home)
